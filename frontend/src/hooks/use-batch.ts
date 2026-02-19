import { useCallback, useEffect, useMemo, useState } from "react"
import { isJobActive, isJobComplete } from "@/lib/job-status"
import type { JobResponse } from "@/lib/jobs"
import { fetchBatch } from "@/lib/jobs"

interface UseBatchOptions {
  pollIntervalMs?: number
}

export function useBatch(batchId: string | null, options: UseBatchOptions = {}) {
  const pollIntervalMs = options.pollIntervalMs ?? 4000
  const [jobs, setJobs] = useState<JobResponse[]>([])
  const [error, setError] = useState<string | null>(null)
  const [isLoading, setIsLoading] = useState(false)

  const loadBatch = useCallback(async () => {
    if (!batchId) return
    setIsLoading(true)
    try {
      const data = await fetchBatch(batchId)
      setJobs(data)
      setError(null)
    } catch (err) {
      setJobs([])
      setError(err instanceof Error ? err.message : "Failed to load batch")
    } finally {
      setIsLoading(false)
    }
  }, [batchId])

  useEffect(() => {
    if (!batchId) {
      setJobs([])
      setError(null)
      setIsLoading(false)
      return
    }
    void loadBatch()
  }, [batchId, loadBatch])

  const hasActiveJobs = useMemo(
    () => jobs.some((job) => isJobActive(job.status)),
    [jobs],
  )

  useEffect(() => {
    if (!batchId || !hasActiveJobs) return
    const interval = window.setInterval(() => {
      void loadBatch()
    }, pollIntervalMs)
    return () => window.clearInterval(interval)
  }, [batchId, hasActiveJobs, loadBatch, pollIntervalMs])

  const isComplete = useMemo(
    () => jobs.length > 0 && jobs.every((job) => isJobComplete(job.status)),
    [jobs],
  )

  const shouldShowRunStatus = Boolean(batchId) && (hasActiveJobs || isLoading || jobs.length === 0)

  return {
    jobs,
    error,
    isLoading,
    reload: loadBatch,
    setJobs,
    hasActiveJobs,
    isComplete,
    shouldShowRunStatus,
  }
}
