import type { Browser, BrowserContext, Page } from "playwright";
import { chromium } from "playwright";
import { Type } from "typebox";
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";

const MAX_TEXT = 65_536;
const proxyServer = process.env.ORCHESTRATOR_EGRESS_PROXY;
const runId = process.env.ORCHESTRATOR_RUN_ID;
const proxyToken = process.env.ORCHESTRATOR_EGRESS_TOKEN;

function bounded(value: string): string {
  const bytes = Buffer.from(value, "utf8");
  if (bytes.length <= MAX_TEXT) return value;
  return bytes.subarray(0, MAX_TEXT).toString("utf8") + "\n[truncated]";
}

export default function browserTools(pi: ExtensionAPI) {
  let browser: Browser | undefined;
  let context: BrowserContext | undefined;
  let page: Page | undefined;
  const consoleEntries: string[] = [];
  const networkFailures: string[] = [];

  async function currentPage(): Promise<Page> {
    if (!proxyServer || !runId || !proxyToken) {
      throw new Error("orchestrator browser proxy authority is unavailable");
    }
    if (!browser) {
      browser = await chromium.launch({
        executablePath: "/usr/bin/chromium",
        headless: false,
        env: { ...process.env, DISPLAY: process.env.DISPLAY ?? ":0" },
        proxy: { server: proxyServer, username: runId, password: proxyToken },
        args: [
          "--disable-dev-shm-usage",
          "--disable-features=WebBluetooth,MediaRouter",
          "--disable-file-system",
          "--no-first-run",
        ],
      });
      context = await browser.newContext({
        acceptDownloads: false,
        permissions: [],
        serviceWorkers: "block",
      });
      page = await context.newPage();
      page.on("console", (message) => {
        consoleEntries.push(`${message.type()}: ${message.text()}`);
        if (consoleEntries.length > 200) consoleEntries.shift();
      });
      page.on("requestfailed", (request) => {
        networkFailures.push(
          `${request.method()} ${request.url()} ${request.failure()?.errorText ?? "failed"}`,
        );
        if (networkFailures.length > 200) networkFailures.shift();
      });
    }
    return page!;
  }

  pi.registerTool({
    name: "browser_navigate",
    label: "Browser navigate",
    description: "Navigate Chromium through the authenticated, allowlisted guest egress proxy.",
    parameters: Type.Object({ url: Type.String({ maxLength: 4096 }) }),
    async execute(_id, params, signal) {
      const active = await currentPage();
      await active.goto(params.url, { waitUntil: "domcontentloaded", timeout: 30_000 });
      if (signal?.aborted) throw new Error("browser operation cancelled");
      return { content: [{ type: "text", text: bounded(`Loaded ${active.url()}\n${await active.title()}`) }], details: { url: active.url() } };
    },
  });

  pi.registerTool({
    name: "browser_snapshot",
    label: "Browser accessibility snapshot",
    description: "Return a bounded accessibility snapshot of the current guest page.",
    parameters: Type.Object({}),
    async execute() {
      const active = await currentPage();
      const snapshot = await active.locator("body").ariaSnapshot({ timeout: 10_000 });
      return { content: [{ type: "text", text: bounded(snapshot) }], details: { url: active.url() } };
    },
  });

  pi.registerTool({
    name: "browser_click",
    label: "Browser click",
    description: "Click one visible element in the current guest page.",
    parameters: Type.Object({ selector: Type.String({ maxLength: 2048 }) }),
    async execute(_id, params) {
      const active = await currentPage();
      await active.locator(params.selector).click({ timeout: 10_000 });
      return { content: [{ type: "text", text: `Clicked ${params.selector}` }], details: { url: active.url() } };
    },
  });

  pi.registerTool({
    name: "browser_input",
    label: "Browser input",
    description: "Replace the value of one visible form control in the guest page.",
    parameters: Type.Object({
      selector: Type.String({ maxLength: 2048 }),
      text: Type.String({ maxLength: 65_536 }),
    }),
    async execute(_id, params) {
      const active = await currentPage();
      await active.locator(params.selector).fill(params.text, { timeout: 10_000 });
      return { content: [{ type: "text", text: `Filled ${params.selector}` }], details: { url: active.url() } };
    },
  });

  pi.registerTool({
    name: "browser_screenshot",
    label: "Browser screenshot",
    description: "Capture a bounded PNG screenshot and return it to the model.",
    parameters: Type.Object({ full_page: Type.Optional(Type.Boolean()) }),
    async execute(_id, params) {
      const active = await currentPage();
      const image = await active.screenshot({ type: "png", fullPage: params.full_page ?? false });
      if (image.length > 1_048_576) throw new Error("browser screenshot exceeds 1 MiB");
      return {
        content: [
          { type: "text", text: `Screenshot of ${active.url()}` },
          { type: "image", data: image.toString("base64"), mimeType: "image/png" },
        ],
        details: { url: active.url(), size_bytes: image.length },
      };
    },
  });

  pi.registerTool({
    name: "browser_console",
    label: "Browser console",
    description: "Return bounded console messages from the current guest browser session.",
    parameters: Type.Object({}),
    async execute() {
      await currentPage();
      return { content: [{ type: "text", text: bounded(consoleEntries.join("\n") || "No console messages.") }], details: { count: consoleEntries.length } };
    },
  });

  pi.registerTool({
    name: "browser_network",
    label: "Browser network failures",
    description: "Return bounded failed-request metadata from the guest browser session.",
    parameters: Type.Object({}),
    async execute() {
      await currentPage();
      return { content: [{ type: "text", text: bounded(networkFailures.join("\n") || "No network failures.") }], details: { count: networkFailures.length } };
    },
  });

  pi.on("session_shutdown", async () => {
    await context?.close();
    await browser?.close();
  });
}
