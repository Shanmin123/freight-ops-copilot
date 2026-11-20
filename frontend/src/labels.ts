import type { Channel, TicketStatus } from './api/types'

export const STATUS_LABEL: Record<TicketStatus, string> = {
  processing: 'AI 处理中',
  ai_replied: 'AI 已回复',
  awaiting_confirmation: '待你确认',
  replied: '已回复',
  needs_human: '已转人工',
}

export const CHANNEL_LABEL: Record<Channel, string> = {
  web: '网页客服',
  wechat: '微信',
  kakaotalk: 'KakaoTalk',
  email: '邮件',
  web_form: '官网询价表单',
}

export const TOOL_LABEL: Record<string, string> = {
  'crm.get_customer': '查客户档案',
  'shipment.list': '列出客户货物',
  'shipment.get': '查货物轨迹',
  'customs.get_progress': '查清关进度',
  'warehouse.get_job': '查仓库作业',
  'attachment.inspect': '查看附件',
  'customs.submit_pccc': '提交通关号码',
  'warehouse.request_action': '请求仓库操作',
  'ticket.handoff': '转人工处理',
  'kb.search': '检索知识库',
  'quote.create': '创建参考报价',
  'quote.calculate': '运费试算',
  'rate.search': '查运价',
  'schedule.search': '查运输班次',
  'policy.search': '查审批政策',
  'approval.create_case': '提交经理审批',
  'claim.open': '登记货损理赔',
  'delivery.reroute': '登记配送改址',
  'carrier.open_investigation': '登记丢件调查',
  'document.get_findings': '查看单据核对结果',
}
