"""Rendered actor identity for post engagement; no account or session switching.

The observed SDUI picker exposes names and avatars but no profile URLs. Bind the
requested URL on its own rendered page, then require both name and image asset
in the picker. Unknown layouts and missing/ambiguous avatars refuse.

A member who manages no page has no picker at all: the comment box shows only
that member's own avatar. That sole-actor case is bound by the avatar asset
alone, which does not depend on the interface language. If the avatar sits in
a control this module cannot read (a switcher in an unsupported language), it
refuses and reports the control's label instead of guessing.
"""

from linkedin_mcp_server.linkedin.post_scope import REPLY_RANGE_JS

ACTOR_HELPERS_JS = (
    REPLY_RANGE_JS
    + r"""
function actorVisible(node) {
  return node instanceof Element && node.isConnected &&
    getComputedStyle(node).display !== 'none' &&
    getComputedStyle(node).visibility !== 'hidden' && node.getClientRects().length > 0;
}
function avatarKey(src) {
  try {
    const url = new URL(src);
    if (!/(^|\.)licdn\.com$/.test(url.hostname)) return null;
    return url.pathname.match(/^\/dms\/image\/(?:v2\/)?([^/]+)\//)?.[1] || null;
  } catch { return null; }
}
function avatarKeys(node) {
  return [...new Set(Array.from(node.querySelectorAll('img[src]'))
    .map(img => avatarKey(img.src)).filter(Boolean))];
}
function entityPath(anchor) {
  try {
    const url = new URL(anchor.href);
    if (!/(^|\.)linkedin\.com$/.test(url.hostname)) return null;
    return url.pathname.match(/^(\/(?:in|company)\/[^/]+)(?:\/posts)?\/?$/)?.[1] || null;
  } catch { return null; }
}
function linkedIdentity(anchors, path) {
  const same = anchors.filter(node => entityPath(node) === path);
  const names = [...new Set(same.map(node => node.innerText.trim().split('\n')[0].trim()).filter(Boolean))];
  const keys = [...new Set(same.flatMap(avatarKeys))];
  return names.length === 1 && keys.length === 1 ? {path, name: names[0], avatar: keys[0]} : null;
}
// Only the English SDUI labels were observed on 2026-10-06. Other locales
// refuse actor selection rather than guess at controls from their position.
function actorLabels() {
  return {
    en: {switcher: 'Switch to different account', title: 'Comment, react, and repost as',
         select: 'Select ', save: 'Save', cancel: 'Cancel'}
  }[document.documentElement.lang.toLowerCase().split('-')[0]] || null;
}
function actorSwitcher(root) {
  const labels = actorLabels();
  if (!labels || !root?.isConnected) return null;
  const candidates = Array.from(root.querySelectorAll('[aria-label]')).filter(
    node => actorVisible(node) && node.getAttribute('aria-label') === labels.switcher
  );
  return candidates.length === 1 ? candidates[0] : null;
}
function actorPicker() {
  const labels = actorLabels();
  if (!labels) return null;
  const titles = Array.from(document.querySelectorAll('h2')).filter(
    node => actorVisible(node) && node.innerText.trim() === labels.title
  );
  if (titles.length !== 1) return null;
  let container = titles[0].parentElement;
  while (container && container !== document.body) {
    const groups = Array.from(container.querySelectorAll('[role="radiogroup"]')).filter(actorVisible);
    const buttons = Array.from(container.querySelectorAll('button')).filter(actorVisible);
    if (groups.length === 1 && buttons.some(button => button.innerText.trim() === labels.cancel)) {
      return {container, group: groups[0], buttons};
    }
    container = container.parentElement;
  }
  return null;
}
function actorOption(picker, actor) {
  const labels = actorLabels();
  const available = Array.from(picker.group.querySelectorAll('[role="radio"]')).filter(actorVisible);
  const expectedLabel = labels.select + actor.name;
  // The closed switcher exposes only its avatar. If another actor shares that
  // asset, a failed Save or later actor change would pass the dispatch guard.
  if (available.some(node => avatarKeys(node).includes(actor.avatar) &&
      node.getAttribute('aria-label') !== expectedLabel)) return null;
  const options = available.filter(node => {
    const keys = avatarKeys(node);
    return node.getAttribute('aria-label') === expectedLabel &&
      keys.length === 1 && keys[0] === actor.avatar;
  });
  return options.length === 1 ? options[0] : null;
}
// The sole-actor composer avatar: the one avatar asset rendered in the
// smallest container around the post's own comment editor, pinned when the
// actor was bound so a reply editor opened later cannot stand in for it. The climb stops
// before it reaches the action bar or any rendered comment, so the post
// author's and commenters' avatars can never be read as the acting identity.
// `control` is the label of an interactive wrapper around that avatar, which
// is what an account switcher looks like in a language this module lacks.
function soleActorAvatar(root, editor) {
  if (!root?.isConnected || !editor?.isConnected || !root.contains(editor) ||
      !actorVisible(editor)) return null;
  const bar = root.__linkedinMcpPost?.bar;
  let container = editor.parentElement;
  while (container && container !== root && root.contains(container)) {
    if ((bar && container.contains(bar)) ||
        container.querySelector('[id^="replaceableComment_"]')) return null;
    const images = Array.from(container.querySelectorAll('img[src]')).filter(img =>
      actorVisible(img) && !editor.contains(img) && avatarKey(img.src));
    if (images.length > 0) {
      const keys = [...new Set(images.map(img => avatarKey(img.src)))];
      if (keys.length !== 1) return null;
      const wrappers = [...new Set(images.map(img =>
        img.closest('button, [role="button"], [aria-haspopup]')).filter(node =>
        node && container.contains(node)))];
      const control = wrappers.length === 0 ? null :
        ((wrappers[0].getAttribute('aria-label') || wrappers[0].innerText || '').trim() || 'unlabelled');
      return {key: keys[0], control,
        member: images.every(img => img.src.includes('profile-displayphoto'))};
    }
    container = container.parentElement;
  }
  return null;
}
function actorStillMatches(root) {
  const pin = root?.__linkedinMcpPost;
  if (pin?.actorMode === 'sole' && !pin.actorRoot) {
    if (!pin.actor || pin.route !== location.href || actorPicker() || actorSwitcher(root)) return false;
    const sole = soleActorAvatar(root, pin.soleEditor);
    return Boolean(sole && sole.control === null && sole.member && sole.key === pin.actor.avatar);
  }
  if (!pin?.actor || pin.route !== location.href || actorPicker()) return false;
  if (pin.actorRoot) {
    return Boolean(root.isConnected && pin.actorRoot.__linkedinMcpPost === pin.actorPin &&
      actorStillMatches(pin.actorRoot) && replyRangeNodes(pin, true) &&
      pin.replyAvatar?.isConnected && root.contains(pin.replyAvatar) &&
      avatarKey(pin.replyAvatar.src) === pin.actor.avatar);
  }
  const control = actorSwitcher(root);
  if (!control || control !== pin.actorControl) return false;
  const keys = avatarKeys(control);
  return keys.length === 1 && keys[0] === pin.actor.avatar;
}
"""
)

# Text alone does not bind the requested URL to an actor picker entry. The
# heading and avatar come from the same top-card ancestry on that exact page.
READ_ACTOR_IDENTITY_JS = (
    "(actorPath) => {"
    + ACTOR_HELPERS_JS
    + r"""
  const path = actorPath.replace(/\/+$/, '');
  if (path.startsWith('/company/')) {
    // An admin URL can replace the requested vanity route. Rendered author
    // links preserve that exact vanity identity; never infer a numeric alias.
    const linked = linkedIdentity(Array.from(document.querySelectorAll('main a[href]')).filter(actorVisible), path);
    if (linked) return {...linked, path: actorPath};
  }
  if (location.pathname.replace(/\/+$/, '') !== path) return null;
  let headings = Array.from(document.querySelectorAll('main h1')).filter(actorVisible);
  // Current SDUI personal topcards use the first main H2, before section headings.
  if (headings.length === 0 && actorPath.startsWith('/in/')) {
    headings = Array.from(document.querySelectorAll('main h2')).filter(actorVisible).slice(0, 1);
  }
  if (headings.length !== 1) return null;
  const name = headings[0].innerText.trim();
  if (!name) return null;
  const imageKind = actorPath.startsWith('/company/') ? 'company-logo' : 'profile-displayphoto';
  let container = headings[0].parentElement;
  while (container && container.tagName !== 'MAIN') {
    if (Array.from(container.querySelectorAll('h1, h2')).filter(actorVisible).length > 1) return null;
    const images = Array.from(container.querySelectorAll('img[src]')).filter(
      img => actorVisible(img) && img.src.includes(imageKind)
    );
    const keys = [...new Set(images.map(img => avatarKey(img.src)).filter(Boolean))];
    if (keys.length === 1) return {path: actorPath, name, avatar: keys[0]};
    if (keys.length > 1) return null;
    container = container.parentElement;
  }
  return null;
}
"""
)

OPEN_ACTOR_PICKER_JS = (
    "(root) => {"
    + ACTOR_HELPERS_JS
    + r"""
  const control = actorSwitcher(root);
  if (!control || actorPicker()) return false;
  control.click();
  return true;
}
"""
)

SELECT_ACTOR_JS = (
    "({actor}) => {"
    + ACTOR_HELPERS_JS
    + r"""
  const picker = actorPicker();
  if (!picker) return 'pending';
  const option = actorOption(picker, actor);
  if (!option) return 'unavailable';
  const labels = actorLabels();
  if (option.getAttribute('aria-checked') === 'true') {
    const cancel = picker.buttons.filter(button => button.innerText.trim() === labels.cancel);
    if (cancel.length !== 1) return 'unavailable';
    cancel[0].click();
    return 'selected';
  }
  option.click();
  return 'save_required';
}
"""
)

SAVE_ACTOR_JS = (
    "({actor}) => {"
    + ACTOR_HELPERS_JS
    + r"""
  const picker = actorPicker();
  if (!picker) return false;
  const option = actorOption(picker, actor);
  if (!option || option.getAttribute('aria-checked') !== 'true') return false;
  const buttons = picker.buttons.filter(button => button.innerText.trim() === actorLabels().save &&
    !button.disabled && button.getAttribute('aria-disabled') !== 'true');
  if (buttons.length !== 1) return false;
  buttons[0].click();
  return true;
}
"""
)

PIN_ACTOR_JS = (
    "({root, actor}) => {"
    + ACTOR_HELPERS_JS
    + r"""
  const pin = root?.__linkedinMcpPost;
  const control = actorSwitcher(root);
  if (!pin || !control || actorPicker() || pin.route !== location.href) return false;
  const keys = avatarKeys(control);
  if (keys.length !== 1 || keys[0] !== actor.avatar) return false;
  pin.actor = actor;
  pin.actorControl = control;
  return true;
}
"""
)

# A member with no page to act as sees no switcher. Bind the requested personal
# actor to the comment box's own avatar, or refuse with the reason. Nothing is
# clicked or typed here.
PIN_SOLE_ACTOR_JS = (
    "({root, actor}) => {"
    + ACTOR_HELPERS_JS
    + r"""
  const pin = root?.__linkedinMcpPost;
  const lang = document.documentElement.lang || '';
  if (!pin || !root.isConnected || pin.route !== location.href) return {status: 'changed', lang};
  if (!actor?.path?.startsWith('/in/')) return {status: 'unsupported_actor', lang};
  if (actorSwitcher(root) || actorPicker()) return {status: 'switcher_present', lang};
  const editors = Array.from(
    root.querySelectorAll('[role="textbox"][contenteditable="true"]')
  ).filter(actorVisible);
  if (editors.length !== 1) return {status: editors.length === 0 ? 'no_editor' : 'ambiguous_editor', lang};
  const sole = soleActorAvatar(root, editors[0]);
  if (!sole) return {status: 'no_avatar', lang};
  if (sole.control !== null) return {status: 'unknown_switcher', label: sole.control.slice(0, 120), lang};
  if (!sole.member || sole.key !== actor.avatar) return {status: 'avatar_mismatch', lang};
  pin.actor = actor;
  pin.actorMode = 'sole';
  pin.actorControl = null;
  pin.soleEditor = editors[0];
  return {status: 'pinned', lang};
}
"""
)
