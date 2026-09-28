/** Read-only YAML: each line's key (before the first colon) in thread, the rest in ink. */
export function YamlView({ yaml }: { yaml: string }) {
  return (
    <pre className="overflow-x-auto border border-rule p-2 font-mono text-meta">
      {yaml.split("\n").map((line, index) => {
        const colon = line.indexOf(":");
        return (
          <span key={index} className="block">
            {colon < 0 ? (
              line || " "
            ) : (
              <>
                <span className="text-thread">{line.slice(0, colon)}</span>
                <span className="text-ink">{line.slice(colon)}</span>
              </>
            )}
          </span>
        );
      })}
    </pre>
  );
}
