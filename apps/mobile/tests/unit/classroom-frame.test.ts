import {
  checkpointQuestionRef,
  classroomDocument,
  readFrameMessage,
} from "@/features/classroom/frame-document";

const NONCE = "mobile-frame-1234";
const COMPILER =
  '<!doctype html><html><head><meta http-equiv="Content-Security-Policy" content="default-src \'none\'; script-src \'sha256-ZmFrZS1jb21waWxlci1oYXNo=\'"></head><body><main data-slide="slide-1">Synthetic lesson</main><script>window.compiler=true;</script></body></html>';
const slides = [
  { slide_id: "slide-1", order: 1 },
  { slide_id: "slide-2", order: 2 },
];

describe("restricted classroom frame", () => {
  test("compiler HTML is preserved as a string, including its CSP and hashes", () => {
    const html = classroomDocument(COMPILER, NONCE);
    expect(html).toContain('sandbox="allow-scripts"');
    expect(html).not.toContain("allow-same-origin");
    expect(html).toContain("connect-src 'none'");
    expect(html).toContain(
      "script-src 'nonce-mobile-frame-1234' 'sha256-ZmFrZS1jb21waWxlci1oYXNo='",
    );
    const match = /frame\.srcdoc=("(?:[^"\\]|\\.)*");/.exec(html);
    expect(match).not.toBeNull();
    expect(JSON.parse(match![1]!)).toBe(COMPILER);
    expect(html).not.toContain("Bearer ");
  });

  test("listener is installed before loading the compiler document", () => {
    const html = classroomDocument(COMPILER, NONCE);
    expect(html.indexOf("window.addEventListener('message'")).toBeLessThan(
      html.indexOf("frame.srcdoc="),
    );
  });

  test("bridge handshake authenticates its iframe source and isolates native commands", () => {
    const html = classroomDocument(COMPILER, NONCE);
    const script =
      /<script nonce="mobile-frame-1234">([\s\S]*)<\/script><\/body>/.exec(
        html,
      )![1]!;
    type Handler = (event: { source?: unknown; data?: unknown }) => void;
    const listeners: Record<string, Handler> = {};
    const nativeMessages: string[] = [];
    const frame = { srcdoc: "", contentWindow: { postMessage: jest.fn() } };
    const port = {
      onmessage: null as ((event: { data: unknown }) => void) | null,
      start: jest.fn(),
      close: jest.fn(),
      postMessage: jest.fn(),
    };
    const transferredPort = {};
    const window = {
      ReactNativeWebView: {
        postMessage: (value: string) => nativeMessages.push(value),
      },
      addEventListener: (type: string, handler: Handler) => {
        listeners[type] = handler;
      },
      __ntGoto: undefined as
        ((nonce: string, order: number) => void) | undefined,
    };
    class Channel {
      port1 = port;
      port2 = transferredPort;
    }
    new Function("window", "document", "MessageChannel", script)(
      window,
      { getElementById: () => frame },
      Channel,
    );
    expect(frame.srcdoc).toBe(COMPILER);
    listeners.message!({
      source: {},
      data: { type: "classroom_ready", nonce: "compiler-nonce" },
    });
    expect(frame.contentWindow.postMessage).not.toHaveBeenCalled();
    listeners.message!({
      source: frame.contentWindow,
      data: { type: "classroom_ready", nonce: "compiler-nonce" },
    });
    expect(frame.contentWindow.postMessage).toHaveBeenCalledWith(
      { type: "classroom_init", nonce: "compiler-nonce" },
      "*",
      [transferredPort],
    );
    expect(readFrameMessage(nativeMessages[0]!, NONCE, slides)).toEqual({
      type: "ready",
    });
    port.onmessage!({
      data: { type: "page_selected", order: 2, slide_id: "slide-2" },
    });
    expect(readFrameMessage(nativeMessages[1]!, NONCE, slides)).toEqual({
      type: "page_selected",
      order: 2,
      slide_id: "slide-2",
    });
    window.__ntGoto!("wrong-frame", 2);
    expect(port.postMessage).not.toHaveBeenCalled();
    window.__ntGoto!(NONCE, 2);
    expect(port.postMessage).toHaveBeenCalledWith({
      type: "goto_page",
      order: 2,
    });
  });

  test.each(["", "short", 'bad\"nonce', "a".repeat(129)])(
    "rejects invalid native nonce %s",
    (nonce) => {
      expect(() => classroomDocument(COMPILER, nonce)).toThrow(
        "invalid_frame_nonce",
      );
    },
  );

  test("does not silently admit a document without compiler CSP/hash", () => {
    expect(() =>
      classroomDocument("<html><body>untrusted</body></html>", NONCE),
    ).toThrow("untrusted_classroom_document");
    expect(() =>
      classroomDocument(
        '<meta http-equiv="Content-Security-Policy" content="script-src *">',
        NONCE,
      ),
    ).toThrow("untrusted_classroom_document");
  });

  test("the native bridge accepts a known slide with matching nonce only", () => {
    const message = (payload: unknown, nonce = NONCE) =>
      JSON.stringify({ nonce, payload });
    expect(readFrameMessage(message({ type: "ready" }), NONCE, slides)).toEqual(
      { type: "ready" },
    );
    expect(
      readFrameMessage(
        message({ type: "page_selected", order: 2, slide_id: "slide-2" }),
        NONCE,
        slides,
      ),
    ).toEqual({ type: "page_selected", order: 2, slide_id: "slide-2" });
    expect(
      readFrameMessage(
        message({ type: "page_selected", order: 2, slide_id: "slide-1" }),
        NONCE,
        slides,
      ),
    ).toBeNull();
    expect(
      readFrameMessage(
        message({ type: "page_selected", order: 3, slide_id: "slide-3" }),
        NONCE,
        slides,
      ),
    ).toBeNull();
    expect(
      readFrameMessage(
        message({ type: "ready" }, "stale-frame-1234"),
        NONCE,
        slides,
      ),
    ).toBeNull();
    expect(
      readFrameMessage(
        message({ type: "navigate", url: "https://example.invalid" }),
        NONCE,
        slides,
      ),
    ).toBeNull();
    expect(readFrameMessage("{bad", NONCE, slides)).toBeNull();
    expect(readFrameMessage(" ".repeat(4097), NONCE, slides)).toBeNull();
  });

  test.each([
    [{ question_id: "question-1", question_revision: 2 }, "question-1:2"],
    [{ question_id: "question-1" }, null],
    [{ question_id: "", question_revision: 1 }, null],
    [{ question_id: "question-1", question_revision: 0 }, null],
    [{ question_id: "question-1", question_revision: 1.5 }, null],
  ])(
    "checkpoint references bind the public question to its revision",
    (question, expected) => {
      expect(checkpointQuestionRef(question as Record<string, unknown>)).toBe(
        expected,
      );
    },
  );
});
