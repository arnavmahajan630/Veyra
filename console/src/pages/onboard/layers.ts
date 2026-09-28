/** control-api's classification: contract-envelope layers, e.g. `[{syslog: {…}}, {json: {text_field: "msg"}}]`. */
export type EnvelopeLayer = Record<string, Record<string, unknown>>;

/** "syslog → json (msg) → text": each layer peeled, then the text the templates match. */
export function describeLayers(layers: readonly EnvelopeLayer[]): string {
  const names = layers.flatMap((layer) =>
    Object.entries(layer).map(([name, options]) => {
      const field = options.text_field;
      return typeof field === "string" ? `${name} (${field})` : name;
    }),
  );
  return [...names, "text"].join(" → ");
}

/** `src_<slug>_01` from a display name (C6 task 4); the slug also serves as the vendor. */
export function slug(name: string): string {
  return name
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "_")
    .replace(/^_+|_+$/g, "");
}
