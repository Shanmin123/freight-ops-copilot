import { useEffect, useRef, useState } from "react";
import type { FormEvent } from "react";

import { getConversation, getCustomers, postChatMessage } from "../api/client";
import type { Channel, Conversation, Customer, Message } from "../api/types";
import { MessageText } from "../components/MessageText";
import { useEventStream } from "../hooks/useEventStream";
import { CHAT_CHANNEL_LABEL, CHAT_TEXT, type ChatLanguage } from "../i18n";

function mergeSent(messages: Message[], message: Message): Message[] {
  if (message.status !== "sent") {
    return messages;
  }
  return [...messages.filter((item) => item.id !== message.id), message].sort((a, b) => a.id - b.id);
}

interface ChatConversationProps {
  customerId: string;
  channel: Channel;
  language: ChatLanguage;
  reload: () => void;
}

function ChatConversation({ customerId, channel, language, reload }: ChatConversationProps) {
  const [conversation, setConversation] = useState<Conversation | null>(null);
  const [text, setText] = useState("");
  const [typing, setTyping] = useState(false);
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<"loadConversationError" | "sendError" | null>(null);
  const log = useRef<HTMLDivElement>(null);
  const strings = CHAT_TEXT[language];

  useEffect(() => {
    let active = true;
    getConversation(customerId, channel).then(
      (snapshot) => {
        if (active) {
          setConversation({ ...snapshot, messages: snapshot.messages.filter((message) => message.status === "sent") });
        }
      },
      () => { if (active) setError("loadConversationError"); },
    );
    return () => { active = false; };
  }, [channel, customerId]);

  const ticketId = conversation?.ticket_id;
  useEventStream(ticketId && conversation
    ? "/api/chat/tickets/" + encodeURIComponent(ticketId) + "/events?since=" + conversation.last_seq
    : null, (event) => {
    if (event.type === "demo.reset") {
      reload();
    } else if (event.ticket_id === ticketId) {
      if (event.type === "ticket.typing") {
        setTyping(event.payload.typing === true);
      } else if (event.type === "message.created" || event.type === "message.updated") {
        const message = event.payload.message;
        if (message) {
          setConversation((current) => current && { ...current, messages: mergeSent(current.messages, message) });
        }
      }
    }
  });

  useEffect(() => {
    if (log.current) {
      log.current.scrollTop = log.current.scrollHeight;
    }
  }, [conversation?.messages, typing]);

  async function send(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const trimmed = text.trim();
    if (!trimmed || !conversation || sending) {
      return;
    }

    setError(null);
    setSending(true);
    try {
      const result = await postChatMessage(customerId, channel, trimmed);
      // Keep the snapshot cursor until the first connection can replay a fast reply.
      setConversation((current) => current && ({
        ...current,
        ticket_id: result.ticket_id,
        messages: mergeSent(current.messages, result.message),
      }));
      setText("");
    } catch {
      setError("sendError");
    } finally {
      setSending(false);
    }
  }

  const formatTime = new Intl.DateTimeFormat(language === "ko" ? "ko-KR" : "zh-CN", {
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
    timeZone: "Asia/Seoul",
  });

  return (
    <>
      <div className="chat-messages" ref={log} role="log" aria-label={strings.messages}>
        {!conversation && !error && <p className="chat-empty">{strings.loading}</p>}
        {conversation?.messages.length === 0 && !typing && <p className="chat-empty">{strings.empty}</p>}
        {conversation?.messages.map((message) => (
          <div key={message.id} className={
            "chat-message chat-message--" + message.role + (message.kind === "ack" ? " chat-message--ack" : "")
          }>
            <MessageText role={message.role} text={message.text} />
            {message.sent_at && <time dateTime={message.sent_at}>{formatTime.format(new Date(message.sent_at))}</time>}
          </div>
        ))}
        {typing && <p className="chat-typing">{strings.typing}</p>}
      </div>
      {error && (
        <p className="chat-error" role="alert">
          {strings[error]}{" "}
          {error === "loadConversationError" && <button type="button" onClick={reload}>{strings.retry}</button>}
        </p>
      )}
      <form className="chat-composer" onSubmit={send}>
        <textarea
          aria-label={strings.placeholder}
          disabled={!conversation || sending}
          placeholder={strings.placeholder}
          rows={2}
          value={text}
          onChange={(event) => setText(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
              event.preventDefault();
              event.currentTarget.form?.requestSubmit();
            }
          }}
        />
        <button type="submit" disabled={!conversation || sending || !text.trim()}>{strings.send}</button>
      </form>
    </>
  );
}

export function ChatPage() {
  const [customers, setCustomers] = useState<Customer[]>([]);
  const [customerId, setCustomerId] = useState("");
  const [channel, setChannel] = useState<Channel>("web");
  const [language, setLanguage] = useState<ChatLanguage>("zh");
  const [revision, setRevision] = useState(0);
  const [customerError, setCustomerError] = useState(false);
  const [customerAttempt, setCustomerAttempt] = useState(0);
  const strings = CHAT_TEXT[language];

  useEffect(() => {
    let active = true;
    getCustomers().then(
      (items) => {
        if (active) {
          setCustomers(items);
          setCustomerId(items[0]?.id ?? "");
          setLanguage(items[0]?.language === "ko" ? "ko" : "zh");
        }
      },
      () => { if (active) setCustomerError(true); },
    );
    return () => { active = false; };
  }, [customerAttempt]);

  function selectCustomer(id: string) {
    setCustomerId(id);
    setLanguage(customers.find((customer) => customer.id === id)?.language === "ko" ? "ko" : "zh");
  }

  return (
    <main className="chat-page" lang={language === "ko" ? "ko" : "zh-CN"}>
      <div className="chat-toolbar">
        <div className="chat-controls">
          <label>
            {strings.customer}
            <select value={customerId} onChange={(event) => selectCustomer(event.target.value)} disabled={!customers.length}>
              {customers.map((customer) => <option key={customer.id} value={customer.id}>{customer.name}</option>)}
            </select>
          </label>
          <label>
            {strings.channel}
            <select value={channel} onChange={(event) => setChannel(event.target.value as Channel)}>
              {Object.entries(CHAT_CHANNEL_LABEL[language]).map(([id, label]) => (
                <option key={id} value={id}>{label}</option>
              ))}
            </select>
          </label>
        </div>
        <div className="chat-language-control">
          <span id="chat-language-label">{strings.interfaceLanguage}</span>
          <div className="chat-languages" role="group" aria-labelledby="chat-language-label">
            <button type="button" lang="zh-CN" aria-pressed={language === "zh"} onClick={() => setLanguage("zh")}>中文</button>
            <button type="button" lang="ko" aria-pressed={language === "ko"} onClick={() => setLanguage("ko")}>한국어</button>
          </div>
        </div>
      </div>
      <section className="chat-window" aria-labelledby="chat-title">
        <header className="chat-window-header">
          <h1 id="chat-title">{strings.title}</h1>
          <span>{strings.team}</span>
        </header>
        {customerId ? (
          <ChatConversation
            key={customerId + ":" + channel + ":" + revision}
            customerId={customerId}
            channel={channel}
            language={language}
            reload={() => setRevision((current) => current + 1)}
          />
        ) : customerError ? (
          <p className="chat-error" role="alert">
            {strings.loadCustomersError}{" "}
            <button type="button" onClick={() => {
              setCustomerError(false);
              setCustomerAttempt((attempt) => attempt + 1);
            }}>{strings.retry}</button>
          </p>
        ) : (
          <p className="chat-empty">{strings.loading}</p>
        )}
      </section>
    </main>
  );
}

export default ChatPage;
