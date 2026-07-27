import {
  Bar, BarChart, CartesianGrid, Line, LineChart, ResponsiveContainer,
  Tooltip, XAxis, YAxis,
} from 'recharts'
import type { DashboardSeries } from './types'

interface TelemetryChartProps {
  series: DashboardSeries
  title: string
  formatValue: (value: unknown, unit?: string | null) => string
}

export default function TelemetryChart({
  series,
  title,
  formatValue,
}: TelemetryChartProps) {
  const chronological = [...series.points].reverse()
  const isTimeline = series.metric.includes('day')
    || ['success_rate', 'failed_tasks', 'learning_events', 'context_waste']
      .includes(series.metric)
  const common = {
    data: chronological,
    margin: { top: 12, right: 12, left: 0, bottom: 4 },
  }
  const children = (
    <>
      <CartesianGrid stroke="rgba(148, 163, 184, .12)" vertical={false} />
      <XAxis dataKey="key" tick={{ fill: '#8793a7', fontSize: 11 }} tickLine={false} />
      <YAxis
        tick={{ fill: '#8793a7', fontSize: 11 }}
        tickLine={false}
        axisLine={false}
        width={48}
      />
      <Tooltip
        contentStyle={{
          background: '#111927', border: '1px solid #2a3749',
          borderRadius: 10, color: '#f2f6fb',
        }}
        formatter={(value) => formatValue(value, series.unit)}
      />
      {isTimeline
        ? <Line type="monotone" dataKey="value" stroke="#6ee7d2" strokeWidth={2} dot={{ r: 3 }} connectNulls={false} />
        : <Bar dataKey="value" fill="#6ee7d2" radius={[5, 5, 0, 0]} />}
    </>
  )
  return (
    <div className="chart" role="img" aria-label={`${title} chart`}>
      <ResponsiveContainer width="100%" height="100%">
        {isTimeline
          ? <LineChart {...common}>{children}</LineChart>
          : <BarChart {...common}>{children}</BarChart>}
      </ResponsiveContainer>
    </div>
  )
}
