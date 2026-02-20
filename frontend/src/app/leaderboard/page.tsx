"use client"

import { ArrowLeft01Icon } from "@hugeicons/core-free-icons"
import { HugeiconsIcon } from "@hugeicons/react"
import Link from "next/link"
import { useEffect, useState } from "react"
import {
  Bar,
  BarChart,
  Cell,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts"
import { AppFooter } from "@/components/app-footer"
import { AppHeader } from "@/components/app-header"
import { Badge } from "@/components/ui/badge"
import { Card, CardContent, CardHeader } from "@/components/ui/card"
import { Skeleton } from "@/components/ui/skeleton"
import { API_BASE } from "@/lib/api"

interface SeverityBreakdown {
  critical: number
  high: number
  medium: number
  low: number
  info: number
}

interface ModelStats {
  model: string
  total_jobs: number
  succeeded_jobs: number
  failed_jobs: number
  success_rate: number
  avg_duration_seconds: number | null
  total_vulnerabilities: number
  avg_vulnerabilities_per_audit: number
  severity_breakdown: SeverityBreakdown
}

interface LeaderboardResponse {
  models: ModelStats[]
  total_audits: number
  last_updated: string
}

// Grey shades for pie chart segments
const GREY_SHADES = [
  "#ffffff",
  "#e5e5e5",
  "#cccccc",
  "#b3b3b3",
  "#999999",
  "#808080",
  "#666666",
  "#4d4d4d",
]

function formatDuration(seconds: number | null): string {
  if (seconds === null) return "-"
  if (seconds < 60) return `${Math.round(seconds)}s`
  const mins = Math.floor(seconds / 60)
  const secs = Math.round(seconds % 60)
  return `${mins}m ${secs}s`
}

function getShortModelName(model: string): string {
  const parts = model.split("/")
  return parts[parts.length - 1]
}

export default function LeaderboardPage() {
  const [data, setData] = useState<LeaderboardResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    async function fetchLeaderboard() {
      try {
        const res = await fetch(`${API_BASE}/v1/leaderboard`)
        if (!res.ok) {
          throw new Error(`Failed to fetch leaderboard: ${res.status}`)
        }
        const json = await res.json()
        setData(json)
      } catch (err) {
        setError(err instanceof Error ? err.message : "Failed to load leaderboard")
      } finally {
        setLoading(false)
      }
    }
    fetchLeaderboard()
  }, [])

  // Prepare chart data
  const vulnsBarData = data?.models.map((m) => ({
    name: getShortModelName(m.model),
    vulnerabilities: m.total_vulnerabilities,
  })) ?? []

  // Pie chart: audits by model
  const modelsPieData = data?.models.map((m, idx) => ({
    name: getShortModelName(m.model),
    value: m.total_jobs,
    color: GREY_SHADES[idx % GREY_SHADES.length],
  })) ?? []

  const avgTimeBarData = data?.models
    .filter((m) => m.avg_duration_seconds !== null)
    .map((m) => ({
      name: getShortModelName(m.model),
      seconds: Math.round(m.avg_duration_seconds ?? 0),
    })) ?? []

  const CustomTooltip = ({ active, payload, label }: { active?: boolean; payload?: Array<{ value: number; name?: string }>; label?: string }) => {
    if (active && payload && payload.length) {
      return (
        <div className="rounded border border-border bg-card px-2 py-1 text-xs">
          <p>{label ?? payload[0].name}: {payload[0].value}</p>
        </div>
      )
    }
    return null
  }

  return (
    <main className="flex min-h-screen w-screen flex-col">
      <AppHeader />
      <section className="flex flex-1 flex-col px-6 py-8">
        <div className="mx-auto w-full max-w-6xl">
          <div className="mb-8">
            <div className="flex items-center gap-2 mb-2">
              <Link
                href="/"
                className="text-muted-foreground hover:text-foreground transition-colors"
              >
                <HugeiconsIcon
                  icon={ArrowLeft01Icon}
                  strokeWidth={2}
                  className="size-6"
                />
              </Link>
              <h1 className="text-3xl font-serif text-foreground">
                leaderboard
              </h1>
            </div>
            <p className="text-sm text-muted-foreground">
              model performance benchmarks based on completed audits
            </p>
          </div>

          {loading && (
            <div className="space-y-3">
              {[...Array(5)].map((_, i) => (
                <Skeleton key={i} className="h-16 w-full" />
              ))}
            </div>
          )}

          {error && (
            <div className="rounded-md border border-destructive/50 bg-destructive/10 p-4 text-sm text-destructive">
              {error}
            </div>
          )}

          {data && (
            <>
              <div className="mb-6 flex items-center gap-4 text-sm text-muted-foreground">
                <span>total audits: {data.total_audits}</span>
                <span>last updated: {new Date(data.last_updated).toLocaleString()}</span>
              </div>

              {data.models.length === 0 ? (
                <div className="rounded-md border border-muted p-8 text-center text-muted-foreground">
                  no audit data available yet
                </div>
              ) : (
                <>
                  {/* Charts Section */}
                  <div className="mb-8 grid gap-4 md:grid-cols-2 lg:grid-cols-3">
                    {/* Audits by Model (Pie) */}
                    <Card>
                      <CardHeader className="pb-2">
                        <h3 className="text-sm font-medium text-foreground">audits by model</h3>
                      </CardHeader>
                      <CardContent>
                        <ResponsiveContainer width="100%" height={200}>
                          <PieChart>
                            <Pie
                              data={modelsPieData}
                              cx="50%"
                              cy="50%"
                              innerRadius={40}
                              outerRadius={70}
                              dataKey="value"
                              stroke="hsl(var(--border))"
                              strokeWidth={1}
                            >
                              {modelsPieData.map((entry, index) => (
                                <Cell key={`cell-${index}`} fill={entry.color} />
                              ))}
                            </Pie>
                            <Tooltip content={<CustomTooltip />} />
                          </PieChart>
                        </ResponsiveContainer>
                      </CardContent>
                    </Card>

                    {/* Vulnerabilities by Model */}
                    <Card>
                      <CardHeader className="pb-2">
                        <h3 className="text-sm font-medium text-foreground">vulnerabilities found</h3>
                      </CardHeader>
                      <CardContent>
                        <ResponsiveContainer width="100%" height={200}>
                          <BarChart data={vulnsBarData} layout="vertical">
                            <XAxis type="number" tick={{ fontSize: 10, fill: "#888" }} axisLine={false} tickLine={false} />
                            <YAxis
                              type="category"
                              dataKey="name"
                              tick={{ fontSize: 10, fill: "#888" }}
                              width={80}
                              axisLine={false}
                              tickLine={false}
                            />
                            <Tooltip content={<CustomTooltip />} cursor={false} />
                            <Bar dataKey="vulnerabilities" fill="#888888" radius={[0, 4, 4, 0]} />
                          </BarChart>
                        </ResponsiveContainer>
                      </CardContent>
                    </Card>

                    {/* Average Time */}
                    <Card>
                      <CardHeader className="pb-2">
                        <h3 className="text-sm font-medium text-foreground">avg audit time (seconds)</h3>
                      </CardHeader>
                      <CardContent>
                        <ResponsiveContainer width="100%" height={200}>
                          <BarChart data={avgTimeBarData} layout="vertical">
                            <XAxis type="number" tick={{ fontSize: 10, fill: "#888" }} axisLine={false} tickLine={false} />
                            <YAxis
                              type="category"
                              dataKey="name"
                              tick={{ fontSize: 10, fill: "#888" }}
                              width={80}
                              axisLine={false}
                              tickLine={false}
                            />
                            <Tooltip content={<CustomTooltip />} cursor={false} />
                            <Bar dataKey="seconds" fill="#666666" radius={[0, 4, 4, 0]} />
                          </BarChart>
                        </ResponsiveContainer>
                      </CardContent>
                    </Card>
                  </div>

                  {/* Table */}
                  <div className="overflow-x-auto">
                    <table className="w-full text-sm">
                      <thead>
                        <tr className="border-b text-left text-muted-foreground">
                          <th className="pb-3 pr-4 font-medium">#</th>
                          <th className="pb-3 pr-4 font-medium">model</th>
                          <th className="pb-3 pr-4 font-medium text-right">audits</th>
                          <th className="pb-3 pr-4 font-medium text-right">avg time</th>
                          <th className="pb-3 pr-4 font-medium text-right">vulns found</th>
                          <th className="pb-3 pr-4 font-medium text-right">avg/audit</th>
                          <th className="pb-3 font-medium">severity breakdown</th>
                        </tr>
                      </thead>
                      <tbody>
                        {data.models.map((model, idx) => (
                          <tr
                            key={model.model}
                            className="border-b border-muted/50"
                          >
                            <td className="py-4 pr-4 font-mono text-muted-foreground">
                              {idx + 1}
                            </td>
                            <td className="py-4 pr-4">
                              <span className="font-medium text-foreground">
                                {model.model}
                              </span>
                            </td>
                            <td className="py-4 pr-4 text-right font-mono">
                              {model.total_jobs}
                            </td>
                            <td className="py-4 pr-4 text-right font-mono text-muted-foreground">
                              {formatDuration(model.avg_duration_seconds)}
                            </td>
                            <td className="py-4 pr-4 text-right font-mono">
                              {model.total_vulnerabilities}
                            </td>
                            <td className="py-4 pr-4 text-right font-mono">
                              {model.avg_vulnerabilities_per_audit}
                            </td>
                            <td className="py-4">
                              <div className="flex flex-wrap gap-1">
                                {model.severity_breakdown.critical > 0 && (
                                  <Badge variant="destructive" className="text-xs">
                                    C: {model.severity_breakdown.critical}
                                  </Badge>
                                )}
                                {model.severity_breakdown.high > 0 && (
                                  <Badge className="bg-orange-500 text-xs hover:bg-orange-500">
                                    H: {model.severity_breakdown.high}
                                  </Badge>
                                )}
                                {model.severity_breakdown.medium > 0 && (
                                  <Badge className="bg-yellow-500 text-xs text-black hover:bg-yellow-500">
                                    M: {model.severity_breakdown.medium}
                                  </Badge>
                                )}
                                {model.severity_breakdown.low > 0 && (
                                  <Badge variant="secondary" className="text-xs">
                                    L: {model.severity_breakdown.low}
                                  </Badge>
                                )}
                                {model.severity_breakdown.info > 0 && (
                                  <Badge variant="outline" className="text-xs">
                                    I: {model.severity_breakdown.info}
                                  </Badge>
                                )}
                                {model.total_vulnerabilities === 0 && (
                                  <span className="text-xs text-muted-foreground">-</span>
                                )}
                              </div>
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </>
              )}
            </>
          )}
        </div>
      </section>
      <AppFooter />
    </main>
  )
}
