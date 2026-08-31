import { X } from "../ui/icons";
import { DESTINATION_OPTIONS } from "../../data/workspaceSources";
import type { Distribution } from "../../types";
import { IconButton } from "../ui/IconButton";
import { ScrollingText } from "../ui/ScrollingText";

/**
 * Where a run's result is offered for posting. Note "offered": nothing is ever
 * sent automatically — each run ends in a pending proposal the user approves,
 * even when the task's permission is auto-run. That's a deliberate constraint,
 * so the copy says so out loud.
 */
export function TaskDistributionRows({
  distributions,
  onChange,
}: {
  distributions: Distribution[];
  onChange: (distributions: Distribution[]) => void;
}) {
  const used = new Set(distributions.map((d) => `${d.kind}:${d.target}`));
  const available = DESTINATION_OPTIONS.filter((option) => !used.has(option.id));

  return (
    <div>
      <p className="text-sm font-medium text-primary">Send results to</p>
      <p className="mt-1 text-xs leading-relaxed text-tertiary">
        Each run ends with a confirmation before anything is posted.
      </p>

      {distributions.length > 0 && (
        <div className="mt-2.5 flex flex-col gap-1.5">
          {distributions.map((distribution) => {
            const option = DESTINATION_OPTIONS.find(
              (o) => o.kind === distribution.kind && o.target === distribution.target,
            );
            return (
              <div
                key={`${distribution.kind}:${distribution.target}`}
                className="flex items-center gap-2 rounded-lg border border-subtle/60 px-3 py-2"
              >
                <ScrollingText className="flex-1 text-sm text-primary">
                  {option?.label ?? distribution.target}
                </ScrollingText>
                <IconButton
                  label={`Remove ${option?.label ?? distribution.target}`}
                  size="sm"
                  onClick={() =>
                    onChange(
                      distributions.filter(
                        (d) => !(d.kind === distribution.kind && d.target === distribution.target),
                      ),
                    )
                  }
                >
                  <X className="h-3.5 w-3.5" />
                </IconButton>
              </div>
            );
          })}
        </div>
      )}

      {available.length > 0 && (
        <select
          value=""
          aria-label="Add destination"
          onChange={(event) => {
            const option = DESTINATION_OPTIONS.find((o) => o.id === event.target.value);
            if (option) onChange([...distributions, { kind: option.kind, target: option.target }]);
          }}
          className="mt-2 w-full rounded-lg border border-subtle bg-surface px-2.5 py-1.5 text-sm text-secondary transition-colors duration-150 focus:border-accent/40 focus:outline-none"
        >
          <option value="">Add a destination…</option>
          {available.map((option) => (
            <option key={option.id} value={option.id}>
              {option.label}
            </option>
          ))}
        </select>
      )}
    </div>
  );
}
