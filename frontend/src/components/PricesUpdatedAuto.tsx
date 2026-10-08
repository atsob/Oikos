import { useQuery } from '@tanstack/react-query'
import { PricesUpdated } from '@/components/ui'
import { getPricesFreshness } from '@/lib/api'

// "Prices updated 4 min ago" for any page that shows market prices. Without a securityId it
// reports the scheduler's market-data job (prices and FX rates are refreshed by the same run);
// with one it reports when that security's own prices were last downloaded.
export default function PricesUpdatedAuto({ securityId, className }: { securityId?: number; className?: string }) {
  const { data } = useQuery({
    queryKey: ['prices-freshness', securityId ?? null],
    queryFn: () => getPricesFreshness(securityId),
    refetchInterval: 60_000, staleTime: 30_000,
  })
  if (!data) return null
  const at = (securityId ? data.security_last_download : null) ?? data.job_last_run
  const jobWhen = data.job_last_run
    ? new Date(data.job_last_run).toLocaleString([], { day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit' })
    : null
  const detail = data.job_status === 'error'
    ? `The scheduler's last market-data run (${jobWhen}) reported problems: ${data.job_message ?? ''}.`
    : securityId
      ? (jobWhen ? `The scheduler's market-data job last finished ${jobWhen}${data.job_interval_min ? `, and runs every ${data.job_interval_min} minutes` : ''}.` : undefined)
      : (data.job_interval_min ? `Share prices and FX rates are refreshed together, every ${data.job_interval_min} minutes.` : 'Share prices and FX rates are refreshed together.')
  return <PricesUpdated at={at} detail={detail} className={className} />
}
