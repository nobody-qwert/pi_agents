/** Source is rendered as text, so untrusted HTML never becomes DOM. */
export function SafeMarkdown({ source }: { source: string }) { return <div className="markdown">{source.split("\n").map((line, index) => <p key={index}>{line}</p>)}</div>; }
