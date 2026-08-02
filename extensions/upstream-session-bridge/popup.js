document.getElementById("open-console")?.addEventListener("click", async () => {
  const tabs = await chrome.tabs.query({});
  const existing = tabs.find((tab) => {
    try {
      const url = new URL(tab.url || "");
      return ["127.0.0.1", "localhost"].includes(url.hostname);
    } catch {
      return false;
    }
  });
  if (existing?.id) {
    await chrome.tabs.update(existing.id, { active: true });
    if (existing.windowId) await chrome.windows.update(existing.windowId, { focused: true });
    window.close();
    return;
  }
  await chrome.tabs.create({ url: "http://127.0.0.1:8000" });
  window.close();
});

document.getElementById("reload-extension")?.addEventListener("click", () => {
  chrome.runtime.reload();
});
