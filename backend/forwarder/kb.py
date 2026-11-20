"""Bilingual document search and source links for the local knowledge base."""

import json
import logging
import os
import re
import unicodedata
from collections import Counter
from copy import deepcopy
from dataclasses import dataclass
from math import log1p
from pathlib import Path
from urllib.parse import urlsplit

from forwarder.config import BACKEND_DIR
from forwarder.embeddings import LocalEmbeddings, load_embeddings
from forwarder.language import detect_reply_language
from forwarder.tools.registry import ToolContext, ToolRegistry, ToolSpec

KB_TOOL = "kb.search"
logger = logging.getLogger(__name__)
_LANGUAGES = ("zh", "ko")
_CORPUS = BACKEND_DIR / "data" / "kb" / "articles.json"
_SLUG = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*\Z")
_TERMS = re.compile(r"[a-z0-9]+|[\u3400-\u9fff\uac00-\ud7a3]+")
_CITATION = re.compile(r"\[([0-9]{1,3})\]")
_SOURCE_LINE = re.compile(r"^(?:来源[：:]|출처[：:])\s*\[.*\]\(/kb\?doc=.*\)\s*$")
_FOLLOW_UP = re.compile(r"^(?:那[么麼]?|这[个条种项]|上述|그[런럼건게]|이[건게]|앞에서)")
_NO_EVIDENCE = {
    "zh": "知识库里没有找到足够的资料回答这个问题。",
    "ko": "지식베이스에서 이 질문에 답할 근거를 찾지 못했습니다.",
}


def _normalize(text: str) -> str:
    return unicodedata.normalize("NFKC", text).casefold()


def _compact(text: str) -> str:
    return "".join(_TERMS.findall(_normalize(text)))


def _keywords(text: str) -> list[str]:
    words = _TERMS.findall(_normalize(text))
    features: list[str] = []
    for word in words:
        if word.isascii():
            features.append(word)
        else:
            features.extend(word[index:index + 2] for index in range(len(word) - 1))
            features.extend(word[index:index + 3] for index in range(len(word) - 2))
    return features


@dataclass
class _Section:
    hit: dict
    aliases: tuple[str, ...]
    terms: Counter[str]
    length: int


class KnowledgeBase:
    """An immutable in-process index of a small, curated bilingual corpus."""

    def __init__(self, articles: list[dict], embeddings: LocalEmbeddings | None = None) -> None:
        self._articles: list[dict] = []
        self._sections: list[_Section] = []
        self._embeddings = embeddings
        identifiers: set[str] = set()
        for article in articles:
            article_id = article["id"]
            if not _SLUG.fullmatch(article_id) or article_id in identifiers:
                raise ValueError(f"Invalid or duplicate article ID: {article_id}")
            identifiers.add(article_id)
            published = {key: deepcopy(article[key]) for key in ("id", "title")}
            if article.get("reviewed_on"):
                published["reviewed_on"] = article["reviewed_on"]
            published["sections"] = []
            section_ids: set[str] = set()
            for section in article["sections"]:
                section_id = section["id"]
                if not _SLUG.fullmatch(section_id) or section_id in section_ids:
                    raise ValueError(f"Invalid or duplicate section ID: {article_id}/{section_id}")
                section_ids.add(section_id)
                source = section.get("source")
                if source:
                    url = urlsplit(source["url"])
                    if url.scheme != "https" or not url.hostname or url.username is not None:
                        raise ValueError(f"Invalid source URL: {article_id}/{section_id}")
                for localized in (article["title"], section["title"], section["text"]):
                    if not all(localized[lang].strip() for lang in _LANGUAGES):
                        raise ValueError(f"Missing localized content: {article_id}/{section_id}")
                public_section = {
                    "id": section_id,
                    "title": deepcopy(section["title"]),
                    "text": deepcopy(section["text"]),
                    "source": deepcopy(source),
                }
                published["sections"].append(public_section)
                labels = [*section.get("aliases", ()), *section["title"].values()]
                terms = Counter(_keywords(" ".join([*labels, *section["text"].values()])))
                aliases = {_compact(label) for label in labels}
                self._sections.append(_Section(
                    {
                        "article_id": article_id,
                        "section_id": section_id,
                        "title": published["title"],
                        "section_title": public_section["title"],
                        "text": public_section["text"],
                        "source": public_section["source"],
                    },
                    tuple(sorted(alias for alias in aliases if len(alias) >= 2)),
                    terms,
                    sum(terms.values()),
                ))
            self._articles.append(published)

        frequency = Counter(term for section in self._sections for term in section.terms)
        size = len(self._sections)
        self._idf = {term: log1p((size - count + 0.5) / (count + 0.5)) for term, count in frequency.items()}
        self._average_length = (sum(section.length for section in self._sections) / size) if size else 1.0
        self._passages = tuple(
            f"passage: {s.hit['title'][lang]} {s.hit['section_title'][lang]} {s.hit['text'][lang]}"
            for s in self._sections for lang in _LANGUAGES
        )

    @classmethod
    def load(cls, path: Path = _CORPUS, *, use_vectors: bool = True) -> "KnowledgeBase":
        embeddings = None
        if use_vectors and path == _CORPUS:
            model_path = Path(os.getenv("KB_VECTOR_DIR", str(BACKEND_DIR / "var" / "kb-model")))
            try:
                embeddings = load_embeddings(model_path)
            except Exception:
                logger.warning("Local knowledge model unavailable; using keywords.", exc_info=True)
        articles = json.loads(path.read_text(encoding="utf-8"))
        if path == _CORPUS:
            extra = path.with_name("supplemental.json")
            articles += json.loads(extra.read_text(encoding="utf-8"))
            for article in articles:
                article.setdefault("reviewed_on", "2026-11-20")
        return cls(articles, embeddings)

    @property
    def search_mode(self) -> str:
        return "hybrid" if self._embeddings else "keyword"

    @property
    def articles(self) -> list[dict]:
        return deepcopy(self._articles)

    def search(self, query: str, *, limit: int = 3) -> list[dict]:
        """Require topic evidence from a curated alias, then rank by phrase strength and BM25."""
        compact = _compact(query)
        keywords = set(_keywords(query))
        compact_trigrams = {compact[i:i + 3] for i in range(len(compact) - 2)}
        semantics = self._embeddings.similarity(query, self._passages) if self._embeddings and compact and self._sections else []
        ranked: list[tuple[float, _Section]] = []
        for index, section in enumerate(self._sections):
            matches = [_alias_strength(alias, compact, compact_trigrams) for alias in section.aliases]
            evidence = max(matches, default=0.0)
            if evidence == 0:
                continue
            length_factor = 1.2 * (0.25 + 0.75 * section.length / max(1, self._average_length))
            bm25 = sum(
                self._idf[term] * section.terms[term] * 2.2 / (section.terms[term] + length_factor)
                for term in keywords & section.terms.keys()
            )
            vector_bonus = max(0.0, semantics[index] - 0.72) * 5 if semantics else 0.0
            ranked.append((evidence + min(bm25, 30) * 0.12 + vector_bonus, section))
        ranked.sort(key=lambda item: (-item[0], item[1].hit["article_id"], item[1].hit["section_id"]))
        if not ranked:
            if semantics:
                leaders = sorted(enumerate(semantics), key=lambda item: -item[1])
                best, score = leaders[0]
                runner_up = leaders[1][1] if len(leaders) > 1 else 0
                if score >= 0.87 and score - runner_up >= 0.035:
                    return [deepcopy(self._sections[best].hit)]
            return []
        threshold = ranked[0][0] * 0.69
        return [deepcopy(section.hit) for score, section in ranked[:limit] if score >= threshold]


def _alias_strength(alias: str, query: str, query_trigrams: set[str]) -> float:
    if alias in query:
        return 5.0 + min(len(alias), 18) * 0.55
    if len(alias) < 5:
        return 0.0
    grams = {alias[i:i + 3] for i in range(len(alias) - 2)}
    overlap = len(grams & query_trigrams)
    return len(alias) * 0.53 * overlap / len(grams) if overlap >= 3 and overlap / len(grams) >= 0.7 else 0.0


def register_knowledge_tool(registry: ToolRegistry, kb: KnowledgeBase) -> None:
    def search(arguments: dict, context: ToolContext) -> dict:
        rewritten = arguments["query"]
        effective = context.latest_message[:500].strip() or rewritten
        hits = kb.search(effective)
        # A short follow-up may need the topic inferred from the conversation. Prefer the
        # customer's actual wording whenever it contains enough evidence to retrieve.
        if not hits and effective != rewritten and _FOLLOW_UP.match(effective):
            effective = rewritten
            hits = kb.search(effective)
        return {"query": effective, "hits": [
            {"ref": number, **hit} for number, hit in enumerate(hits, 1)
        ]}

    registry.register(ToolSpec(
        name=KB_TOOL,
        description=(
            "检索中韩文知识库。用于询问清关规则、术语、资料、通关号码的办理或更正流程，以及仓库操作说明。"
            "不查询某票货物的实时状态，不执行业务操作。query 使用客户的具体问题，保留主题与疑问。"
        ),
        parameters={
            "type": "object",
            "properties": {"query": {"type": "string", "minLength": 1, "maxLength": 500}},
            "required": ["query"],
            "additionalProperties": False,
        },
        kind="read",
        handler=search,
    ))


def strip_source_lines(text: str) -> str:
    return "\n".join(line for line in text.split("\n") if not _SOURCE_LINE.fullmatch(line.strip())).strip()


def knowledge_answer(model_text: str, hits: list[dict], language: str) -> str:
    """Build references from the retrieved corpus, or use the source excerpt if citations are unusable."""
    if not hits:
        return _NO_EVIDENCE[language]
    text = strip_source_lines(model_text)
    numbers = list(dict.fromkeys(int(number) for number in _CITATION.findall(text)))
    if numbers and detect_reply_language([text]) == language and all(1 <= number <= len(hits) for number in numbers):
        cited = [hits[number - 1] for number in numbers]
    else:
        cited = hits[:1]
        text = cited[0]["text"][language]
    text = _CITATION.sub("", text).strip()
    if not text:
        cited = hits[:1]
        text = cited[0]["text"][language]
    label = "출처: " if language == "ko" else "来源："
    lang_arg = "&lang=ko" if language == "ko" else ""
    links = [
        f"{label}[{hit['title'][language]} · {hit['section_title'][language]}]"
        f"(/kb?doc={hit['article_id']}{lang_arg}#{hit['section_id']})"
        for hit in cited
    ]
    return text + "\n\n" + "\n".join(links)
