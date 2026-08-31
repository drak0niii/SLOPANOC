import { MOCK_USAGE } from "../../../data/mock";

function BreakdownGroup({
  title,
  items,
}: {
  title: string;
  items: { label: string; percent: number }[];
}) {
  return (
    <div>
      <h4 className="text-sm font-medium text-tertiary">{title}</h4>
      <div className="mt-2.5 flex flex-col gap-2">
        {items.map((item) => (
          <div key={item.label}>
            <div className="flex items-center justify-between text-sm">
              <span className="text-secondary">{item.label}</span>
              <span className="text-tertiary">{item.percent}%</span>
            </div>
            <div className="mt-1 h-1.5 overflow-hidden rounded-full bg-surface-hover">
              <div
                className="h-full rounded-full bg-accent/70"
                style={{ width: `${item.percent}%` }}
              />
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

export function UsagePanel() {
  const usage = MOCK_USAGE;
  const percentUsed = Math.min(100, Math.round((usage.messagesUsed / usage.messagesLimit) * 100));

  return (
    <div>
      <h3 className="text-base font-medium text-primary">Usage</h3>
      <p className="mt-1 text-sm text-tertiary">{usage.periodLabel}</p>

      <div className="mt-5 rounded-xl border border-subtle/50 px-4 py-3.5">
        <div className="flex items-baseline justify-between">
          <span className="text-base text-secondary">Messages</span>
          <span className="text-base font-medium text-primary">
            {usage.messagesUsed.toLocaleString()}{" "}
            <span className="text-tertiary">/ {usage.messagesLimit.toLocaleString()}</span>
          </span>
        </div>
        <div className="mt-2.5 h-2 overflow-hidden rounded-full bg-surface-hover">
          <div className="h-full rounded-full bg-accent" style={{ width: `${percentUsed}%` }} />
        </div>
      </div>

      <div className="mt-6 grid grid-cols-2 gap-6">
        <BreakdownGroup title="Models" items={usage.modelBreakdown} />
        <BreakdownGroup title="Thinking" items={usage.thinkingBreakdown} />
      </div>

      <div className="mt-6">
        <h4 className="text-sm font-medium text-tertiary">Connectors</h4>
        <div className="mt-2.5 flex flex-col gap-1.5">
          {usage.connectorBreakdown.map((item) => (
            <div key={item.label} className="flex items-center justify-between text-sm">
              <span className="text-secondary">{item.label}</span>
              <span className="text-tertiary">{item.actions} actions</span>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
