/** Reverse a posted document: ask once, then post a mirror entry dated today. */
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useRef } from 'react'
import { useTranslation } from 'react-i18next'
import { toast } from 'sonner'

import { unwrap } from '@/api/client'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { newKey } from '@/lib/idempotency'

type Post = (headers: { 'Idempotency-Key': string }) => Promise<{ data?: unknown; error?: unknown; response: Response }>

export function ReverseButton({
  docNo,
  status,
  allowed,
  post,
}: {
  docNo: string
  status: string
  allowed: boolean
  post: Post
}) {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  // The same key while the outcome is unknown, so a retry cannot reverse twice.
  const key = useRef(newKey())
  const reverse = useMutation({
    mutationFn: () => unwrap(post({ 'Idempotency-Key': key.current })),
    onSuccess: async () => {
      toast.success(t('reversal.done', { doc: docNo }))
      await queryClient.invalidateQueries()
    },
    onError: (error) => {
      key.current = newKey()
      toast.error(error.message)
    },
  })
  if (status === 'reversed') return <Badge>{t('reversal.reversed')}</Badge>
  if (status !== 'posted' || !allowed) return null
  return (
    <Button
      size="sm"
      variant="ghost"
      loading={reverse.isPending}
      onClick={() => {
        if (window.confirm(t('reversal.confirm', { doc: docNo }))) reverse.mutate()
      }}
    >
      {t('reversal.reverse')}
    </Button>
  )
}
