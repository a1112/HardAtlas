type ShortcutEvent = Pick<KeyboardEvent,
  "key" | "ctrlKey" | "metaKey" | "altKey" | "shiftKey" |
  "defaultPrevented" | "isComposing" | "repeat">;

export function windowShortcut(event: ShortcutEvent): string | undefined {
  if (event.defaultPrevented || event.isComposing || event.repeat || event.altKey) {
    return undefined;
  }
  const key = event.key.toLowerCase();
  if (event.ctrlKey && event.metaKey && !event.shiftKey && key === "f") {
    return "fullscreen";
  }
  if (event.ctrlKey !== event.metaKey && !event.shiftKey) {
    if (key === "m") return "minimize";
    if (key === "w") return "close";
    if (key === "q") return "quit";
  }
  if (key === "f11" && !event.ctrlKey && !event.metaKey && !event.shiftKey) {
    return "fullscreen";
  }
  return undefined;
}

