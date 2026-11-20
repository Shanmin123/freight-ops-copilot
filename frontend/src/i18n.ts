import type { Channel } from "./api/types";
import { CHANNEL_LABEL } from "./labels";

export type ChatLanguage = "zh" | "ko";

export const CHAT_CHANNEL_LABEL: Record<ChatLanguage, Record<Channel, string>> = {
  zh: CHANNEL_LABEL,
  ko: {
    web: "웹 채팅",
    wechat: "위챗",
    kakaotalk: "카카오톡",
    email: "이메일",
    web_form: "홈페이지 견적 문의",
  },
};

export const CHAT_TEXT = {
  zh: {
    title: "货运在线客服",
    placeholder: "请输入您的问题…",
    send: "发送",
    typing: "客服正在输入…",
    customer: "客户",
    channel: "渠道",
    interfaceLanguage: "界面语言",
    team: "货运客服",
    messages: "聊天记录",
    empty: "输入提单号和您的问题，开始咨询。",
    loading: "正在加载对话…",
    loadCustomersError: "未能加载客户列表。",
    loadConversationError: "未能加载对话。",
    sendError: "发送失败，请重试。",
    retry: "重试",
  },
  ko: {
    title: "물류 고객센터",
    placeholder: "문의 내용을 입력하세요…",
    send: "보내기",
    typing: "상담원이 입력 중…",
    customer: "고객",
    channel: "채널",
    interfaceLanguage: "화면 언어",
    team: "물류 상담",
    messages: "채팅 기록",
    empty: "선하증권 번호와 문의 내용을 입력해 주세요.",
    loading: "대화 내용을 불러오는 중…",
    loadCustomersError: "고객 목록을 불러오지 못했습니다.",
    loadConversationError: "대화 내용을 불러오지 못했습니다.",
    sendError: "메시지를 보내지 못했습니다. 다시 시도해 주세요.",
    retry: "다시 시도",
  },
} satisfies Record<ChatLanguage, Record<string, string>>;
