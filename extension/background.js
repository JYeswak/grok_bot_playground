chrome.runtime.onInstalled.addListener(() => {
  chrome.contextMenus.removeAll(() => {
    chrome.contextMenus.create({
      id: "page-to-grok-bot",
      title: "Make a Grok Bot from this page",
      contexts: ["page", "selection", "action"],
    });
  });
  if (chrome.sidePanel?.setPanelBehavior) {
    chrome.sidePanel.setPanelBehavior({ openPanelOnActionClick: true }).catch(() => {});
  }
});

async function openPanel(tab) {
  if (!tab?.id || !chrome.sidePanel?.open) return;
  try {
    await chrome.sidePanel.open({ tabId: tab.id });
  } catch {
    try {
      await chrome.sidePanel.open({ windowId: tab.windowId });
    } catch {
      /* ignore */
    }
  }
}

chrome.action.onClicked.addListener((tab) => {
  openPanel(tab);
});

chrome.contextMenus.onClicked.addListener((info, tab) => {
  if (info.menuItemId === "page-to-grok-bot") openPanel(tab);
});
