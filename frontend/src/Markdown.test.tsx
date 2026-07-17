import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { SafeMarkdown } from "./Markdown";

describe("SafeMarkdown", () => {
  it("renders HTML as text", () => {
    const html = renderToStaticMarkup(<SafeMarkdown source={'<img src=x onerror="alert(1)">'} />);
    expect(html).toContain("&lt;img");
    expect(html).not.toContain("<img");
  });
});
