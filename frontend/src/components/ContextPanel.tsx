import { useEffect, useState } from 'react'
import { getCustomerSummary } from '../api/client'
import type { CustomerSummary } from '../api/types'

const shipmentMode: Record<string, string> = { express_ferry: '轮渡快件', ferry_lcl: '轮渡拼箱' }
const shipmentStatus: Record<string, string> = {
  customs_hold: '清关暂扣', customs_inspection: '海关查验', in_transit: '运输中', at_warehouse: '已到仓',
}
const currency = new Intl.NumberFormat('zh-CN', { style: 'currency', currency: 'USD', currencyDisplay: 'code', maximumFractionDigits: 2, minimumFractionDigits: 0 })

type Props = { customerId: string | null, refreshKey: number }

export function ContextPanel({ customerId, refreshKey }: Props) {
  const [context, setContext] = useState<CustomerSummary | null>(null)
  const [error, setError] = useState('')

  useEffect(() => {
    if (!customerId) return
    let active = true
    void getCustomerSummary(customerId).then((summary) => {
      if (active) {
        setContext(summary)
        setError('')
      }
    }).catch(() => {
      if (active) setError('客户资料加载失败。')
    })
    return () => { active = false }
  }, [customerId, refreshKey])

  const summary = context?.customer.id === customerId ? context : null
  const customer = summary?.customer

  return <section className="wb-context" aria-labelledby="wb-context-heading">
    <h2 id="wb-context-heading">客户与货物</h2>
    {error && <p className="wb-error" role="alert">{error}</p>}
    {!customer && !error && (
      <p className="wb-placeholder">{customerId ? '正在加载客户资料…' : '选择工单后查看'}</p>
    )}
    {customer && summary && <>
      <div className="wb-customer">
        <div className="wb-customer-name"><strong>{customer.name}</strong><span className="wb-id">{customer.id}</span></div>
        <dl>
          <div><dt>客户类型</dt><dd>{customer.type === 'seller' ? '跨境电商卖家' : '个人客户'}{customer.tier ? ` · ${customer.tier} 级` : ''}</dd></div>
          <div><dt>结算方式</dt><dd>{customer.settlement === 'monthly' ? '月结' : '预付'}</dd></div>
          {customer.settlement === 'monthly' && <>
            {customer.credit_limit_usd != null && <div><dt>信用额度</dt><dd>{currency.format(customer.credit_limit_usd)}</dd></div>}
            {customer.credit_used_usd != null && <div><dt>已用额度</dt><dd>{currency.format(customer.credit_used_usd)}</dd></div>}
          </>}
        </dl>
      </div>
      <h3>进行中的货物 <span className="wb-section-count">{summary.shipments.length}</span></h3>
      {summary.shipments.length ? <ul className="wb-shipments">
        {summary.shipments.map((shipment) => {
          const hold = shipment.customs?.hold
          return <li key={shipment.hbl} className="wb-shipment">
            <div className="wb-shipment-title"><strong className="wb-id">{shipment.hbl}</strong>
              <span className={`wb-shipment-status${hold?.status === 'open' ? ' wb-shipment-hold' : ''}`}>
                {hold?.status === 'resubmitted' ? '更正资料已提交' : shipmentStatus[shipment.status] ?? shipment.status}
              </span>
            </div>
            <p>{shipmentMode[shipment.mode] ?? shipment.mode}</p>
            {hold && <p className="wb-hold-reason">{hold.reason_zh}</p>}
            {shipment.delivery_reroute && <p>配送改址已登记</p>}
            {shipment.carrier_investigation && <p>承运商调查已开启</p>}
          </li>
        })}
      </ul> : <p className="wb-placeholder">暂无进行中的货物</p>}
    </>}
  </section>
}
