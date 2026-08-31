import { useEffect, useLayoutEffect, useRef } from "react";
import type { Message as MessageType } from "../../types";
import { Message } from "./Message";

/** How close to the bottom (px) still counts as "following the
 * conversation" — leaves room for the gap below the last message so a
 * resting scroll position reads as pinned. */
const NEAR_BOTTOM_PX = 120;

export function MessageList({ messages }: { messages: MessageType[] }) {
  const endRef = useRef<HTMLDivElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const prevChatIdRef = useRef<string | null>(null);
  // Whether new content should pull the view down with it. Updated only on
  // real scroll events, so it always describes where the user was *before*
  // the content grew — measuring after growth would count the newly added
  // height as "distance from the bottom" and wrongly conclude the user had
  // scrolled away (sending a message adds well over NEAR_BOTTOM_PX at once).
  const followRef = useRef(true);
  const chatId = messages[0]?.chatId ?? null;

  // Jump straight to the bottom, no animation, whenever a different chat's
  // messages are shown (including the very first render) — smooth-scrolling
  // in from the top on every open reads as a glitch for long threads; only
  // genuinely new content while you're already here should animate.
  useLayoutEffect(() => {
    const isChatSwitch = chatId !== prevChatIdRef.current;
    prevChatIdRef.current = chatId;
    if (isChatSwitch) {
      followRef.current = true;
      endRef.current?.scrollIntoView({ behavior: "auto", block: "end" });
    }
  }, [chatId]);

  useEffect(() => {
    const container = containerRef.current;
    const scroller = container?.parentElement;
    if (!container || !scroller) return;

    function updateFollow() {
      if (!scroller) return;
      followRef.current =
        scroller.scrollHeight - scroller.scrollTop - scroller.clientHeight < NEAR_BOTTOM_PX;
    }

    // Follow the conversation as its height grows — new messages, and also a
    // reply's progressive word-by-word reveal (which changes DOM height
    // without this component ever re-rendering, since that state lives inside
    // Message itself — a plain effect keyed on `messages` would miss it
    // entirely). A ResizeObserver catches height changes regardless of cause.
    const observer = new ResizeObserver(() => {
      if (followRef.current) endRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
    });
    observer.observe(container);

    scroller.addEventListener("scroll", updateFollow, { passive: true });
    return () => {
      observer.disconnect();
      scroller.removeEventListener("scroll", updateFollow);
    };
  }, []);

  return (
    <div ref={containerRef} className="mx-auto flex w-full max-w-[900px] flex-col gap-8 px-6 pb-10 pt-8">
      {messages.map((message) => (
        <Message key={message.id} message={message} />
      ))}
      <div ref={endRef} />
    </div>
  );
}
