declare module "@novnc/novnc/lib/rfb" {
  export default class RFB extends EventTarget {
    constructor(target: HTMLElement, url: string, options?: { credentials?: Record<string, string>; shared?: boolean });
    viewOnly: boolean;
    scaleViewport: boolean;
    resizeSession: boolean;
    focusOnClick: boolean;
    disconnect(): void;
  }
}
