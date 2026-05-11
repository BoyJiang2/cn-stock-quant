interface MetricCardProps {
  label: string;
  value: string;
  tone?: "default" | "good" | "bad";
}

export function MetricCard({ label, value, tone = "default" }: MetricCardProps) {
  return (
    <div className={`metricCard ${tone}`}>
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

