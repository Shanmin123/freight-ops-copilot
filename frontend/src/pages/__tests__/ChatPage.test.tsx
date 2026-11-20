import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, test, vi } from "vitest";

import { ChatPage } from "../ChatPage";

class DemoStream extends EventTarget {
  static connections: DemoStream[] = [];
  closed = false;
  private seq = 4;

  constructor(readonly url: string) {
    super();
    DemoStream.connections.push(this);
  }

  receive(type: string, ticketId: string | null, payload: object) {
    act(() => {
      this.dispatchEvent(new MessageEvent(type, {
        data: JSON.stringify({ seq: ++this.seq, type, ticket_id: ticketId, payload, at: "2026-11-19T12:00:00+09:00" }),
      }));
    });
  }

  close() {
    this.closed = true;
  }
}

function respond(body: unknown) {
  return new Response(JSON.stringify(body), { headers: { "Content-Type": "application/json" } });
}

function mockApi({
  language = "zh",
  ticketId = null,
}: { language?: "zh" | "ko"; ticketId?: string | null } = {}) {
  let conversation = { ticket_id: ticketId, messages: [], last_seq: 4 };
  const posted = vi.fn();

  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(input.toString(), "http://localhost");
    if (url.pathname === "/api/customers") {
      const korean = { id: "C-KR-0412", name: "김지우", type: "individual", language: "ko" };
      const chinese = { id: "C-CN-3150", name: "林晓", type: "individual", language: "zh" };
      return respond(language === "ko" ? [korean, chinese] : [chinese, korean]);
    }
    if (url.pathname === "/api/chat/conversation") {
      return respond(conversation);
    }
    if (url.pathname === "/api/chat/messages") {
      const body = JSON.parse(String(init?.body));
      posted(body);
      return respond({
        ticket_id: "T-001042",
        message: { id: 1, role: "customer", text: body.text, status: "sent", kind: "normal" },
      });
    }
    throw new Error("Unexpected request: " + url);
  }));

  return {
    posted,
    resetConversation: () => { conversation = { ticket_id: null, messages: [], last_seq: 8 }; },
  };
}

beforeEach(() => {
  DemoStream.connections = [];
  vi.stubGlobal("EventSource", DemoStream);
});

test("defaults to the selected customer's language and permits a manual override", async () => {
  mockApi({ language: "ko" });
  render(<ChatPage />);
  expect(await screen.findByPlaceholderText("문의 내용을 입력하세요…")).toBeInTheDocument();
  expect(screen.getByRole("combobox", { name: "채널" })).toBeInTheDocument();
  expect(screen.getByRole("option", { name: "웹 채팅" })).toBeInTheDocument();
  expect(screen.getByRole("option", { name: "카카오톡" })).toBeInTheDocument();

  await userEvent.selectOptions(screen.getByRole("combobox", { name: "고객" }), "C-CN-3150");
  expect(screen.getByPlaceholderText("请输入您的问题…")).toBeInTheDocument();
  expect(screen.getByRole("option", { name: "网页客服" })).toBeInTheDocument();

  await userEvent.click(screen.getByRole("button", { name: "한국어" }));
  expect(screen.getByPlaceholderText("문의 내용을 입력하세요…")).toBeInTheDocument();
  expect(screen.getByRole("group", { name: "화면 언어" })).toBeInTheDocument();
});

test("posts to the selected channel and subscribes from the conversation cursor", async () => {
  const { posted } = mockApi();
  render(<ChatPage />);
  const user = userEvent.setup();

  await user.selectOptions(await screen.findByLabelText("渠道"), "wechat");
  await waitFor(() => expect(screen.getByPlaceholderText("请输入您的问题…")).toBeEnabled());
  await user.type(screen.getByPlaceholderText("请输入您的问题…"), "在吗");
  await user.click(screen.getByRole("button", { name: "发送" }));

  expect(posted).toHaveBeenCalledWith({ customer_id: "C-CN-3150", channel: "wechat", text: "在吗" });
  expect(await screen.findByText("在吗")).toBeInTheDocument();
  await waitFor(() => expect(DemoStream.connections.at(-1)?.url).toBe("/api/chat/tickets/T-001042/events?since=4"));
  const stream = DemoStream.connections.at(-1)!;

  stream.receive("message.created", "T-001042", { message: { id: 1, role: "customer", status: "sent", text: "在吗" } });
  stream.receive("ticket.typing", "T-001042", { typing: true });
  expect(await screen.findByText("客服正在输入…")).toBeInTheDocument();

  stream.receive("message.created", "T-001042", { message: { id: 2, role: "agent", status: "draft", text: "等待确认" } });
  expect(screen.queryByText("等待确认")).toBeNull();
  stream.receive("message.updated", "T-001042", { message: { id: 2, role: "agent", status: "sent", text: "您好" } });
  stream.receive("ticket.typing", "T-001042", { typing: false });
  expect(await screen.findByText("您好")).toBeInTheDocument();
  expect(screen.getAllByText("在吗")).toHaveLength(1);
  expect(screen.queryByText("客服正在输入…")).toBeNull();
});

test("refreshes the conversation and closes the previous stream on demo reset", async () => {
  const { resetConversation } = mockApi({ ticketId: "T-001042" });
  render(<ChatPage />);
  await waitFor(() => expect(DemoStream.connections).toHaveLength(1));
  const stream = DemoStream.connections[0];

  stream.receive("message.created", "T-001042", { message: { id: 1, role: "agent", status: "sent", text: "重置前消息" } });
  expect(await screen.findByText("重置前消息")).toBeInTheDocument();
  resetConversation();
  stream.receive("demo.reset", null, {});

  await waitFor(() => expect(screen.queryByText("重置前消息")).toBeNull());
  expect(stream.closed).toBe(true);
});
