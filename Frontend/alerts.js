// Wandering alerts. map.js calls checkForAlerts() with every map
// update; when you or someone in your party wanders off, this shows
// an on-screen banner, vibrates (Android) and, if allowed, sends a
// system notification.
//
// These only fire while the page is open: phones pause web pages
// when the screen is off or the browser is in the background.

// Higher = further from the party.
const ALERT_LEVEL = {
  INSIDE_PARTY: 0,
  BUFFER_ZONE: 1,
  WANDERING: 2,
  FAR_FROM_PARTY: 3,
};
const WANDERING_LEVEL = ALERT_LEVEL.WANDERING;

// GPS can flicker between "buffer" and "wandering"; don't repeat the
// same alert for the same person within this window.
const ALERT_COOLDOWN_MS = 2 * 60 * 1000;
const TOAST_MS = 8000;

const alertEls = {
  toast: document.getElementById("alertToast"),
  toastTitle: document.getElementById("alertToastTitle"),
  toastBody: document.getElementById("alertToastBody"),
  button: document.getElementById("alertsButton"),
};

// person id ("me" for you) -> { level, alertedLevel, alertedAt }
const alertState = new Map();
let toastTimer = null;

const canNotify = "Notification" in window && window.isSecureContext;

// ==================================================
// DECIDING WHEN TO ALERT
// ==================================================

function checkForAlerts(data) {
  checkHelpRequests(data);

  const people = [];

  if (data.me.party) {
    people.push({ id: "me", status: data.me.status, distance: data.me.distanceFromParty });
  }

  // Only friends in my party with a fresh location.
  data.friends.forEach((friend) => {
    if (friend.sameParty && friend.location && friend.ageSeconds <= data.freshSeconds) {
      people.push({
        id: friend.id,
        name: friend.username,
        status: friend.status,
        distance: friend.distanceFromParty,
      });
    }
  });

  const now = Date.now();

  people.forEach((person) => {
    const level = ALERT_LEVEL[person.status];
    const state = alertState.get(person.id);

    // First time we see someone: remember where they are, no alert.
    if (!state) {
      alertState.set(person.id, { level, alertedLevel: 0, alertedAt: 0 });
      return;
    }

    const movedFurther = level >= WANDERING_LEVEL && level > state.level;
    const newAlert = level > state.alertedLevel || now - state.alertedAt > ALERT_COOLDOWN_MS;

    if (movedFurther && newAlert) {
      sendAlert(person, level);
      state.alertedLevel = level;
      state.alertedAt = now;
    } else if (level === ALERT_LEVEL.INSIDE_PARTY && state.alertedLevel >= WANDERING_LEVEL) {
      // Let everyone know they made it back.
      sendAlert(person, level);
      state.alertedLevel = 0;
    }

    state.level = level;
  });
}

function resetAlerts() {
  alertState.clear();
  helpAlerted.clear();
  helpSent = false;
  hideToast();
  hideOkCheck();
  hideHelpAlert();
}

// ==================================================
// SENDING AN ALERT
// ==================================================

function alertText(person, level) {
  const isMe = person.id === "me";
  const who = isMe ? "You're" : `${person.name} is`;
  const meters = Math.round(person.distance);

  if (level === ALERT_LEVEL.INSIDE_PARTY) {
    return { title: `${who} back at the party`, body: "" };
  }

  const where = level === ALERT_LEVEL.FAR_FROM_PARTY ? "far from the party" : "wandering from the party";
  return {
    title: `${who} ${where}`,
    body: `${isMe ? "You are" : `${person.name} is`} ${meters} m from the center of the party.`,
  };
}

function sendAlert(person, level) {
  const { title, body } = alertText(person, level);
  const tone = level === ALERT_LEVEL.FAR_FROM_PARTY ? "far" : level >= WANDERING_LEVEL ? "wandering" : "inside";

  showToast(title, body, tone);

  if (level >= WANDERING_LEVEL && navigator.vibrate) {
    navigator.vibrate([200, 100, 200]);
  }

  // One notification per person, replaced as their status changes.
  systemNotify(title, body, `friendsnme-${person.id}`);

  // When *I* end up far from the party, check that I'm OK.
  if (person.id === "me" && level === ALERT_LEVEL.FAR_FROM_PARTY) {
    showOkCheck(person.distance);
  }
}

async function systemNotify(title, body, tag, extraOptions = {}) {
  if (!canNotify || Notification.permission !== "granted") return;

  const options = { body, tag, renotify: true, ...extraOptions };
  try {
    const registration = await navigator.serviceWorker?.getRegistration();
    if (registration) {
      await registration.showNotification(title, options);
    } else {
      new Notification(title, options);
    }
  } catch (notifyError) {
    console.error("Could not show notification:", notifyError);
  }
}

function showToast(title, body, tone) {
  alertEls.toast.dataset.tone = tone;
  alertEls.toastTitle.textContent = title;
  alertEls.toastBody.textContent = body;
  alertEls.toastBody.hidden = !body;
  alertEls.toast.hidden = false;

  clearTimeout(toastTimer);
  toastTimer = setTimeout(hideToast, TOAST_MS);
}

function hideToast() {
  clearTimeout(toastTimer);
  alertEls.toast.hidden = true;
}

alertEls.toast.addEventListener("click", hideToast);

// ==================================================
// "ARE YOU OK?" CHECK
// ==================================================

// Who to call if you don't answer. Note: this file is public on
// GitHub, so anyone can read this number.
const CHECK_IN_PHONE = "+12678088271";
const CHECK_IN_PHONE_LABEL = "267-808-8271";
const OK_CHECK_SECONDS = 5;

const okEls = {
  overlay: document.getElementById("okCheck"),
  distance: document.getElementById("okCheckDistance"),
  countdown: document.getElementById("okCheckCountdown"),
  okButton: document.getElementById("okCheckButton"),
  callLink: document.getElementById("okCheckCall"),
  help: document.getElementById("okCheckHelp"),
};

let okTimer = null;

okEls.callLink.href = `tel:${CHECK_IN_PHONE}`;
okEls.callLink.textContent = `Call ${CHECK_IN_PHONE_LABEL} now`;

// True once this check-in's help alert went out, until it's resolved.
let helpSent = false;

function showOkCheck(distance, { alreadySent = false } = {}) {
  if (!okEls.overlay.hidden) return;

  helpSent = alreadySent;
  okEls.distance.textContent =
    distance == null ? "" : `You're ${Math.round(distance)} m from your party.`;
  okEls.help.textContent = "";
  okEls.overlay.hidden = false;
  okEls.okButton.focus();
  clearInterval(okTimer);

  if (alreadySent) {
    okEls.countdown.textContent =
      "Your friends were alerted. Tap “I'm OK” to let them know you're fine.";
    return;
  }

  let secondsLeft = OK_CHECK_SECONDS;
  okEls.countdown.textContent =
    `Calling ${CHECK_IN_PHONE_LABEL} in ${secondsLeft}...`;

  okTimer = setInterval(() => {
    secondsLeft -= 1;

    if (secondsLeft > 0) {
      okEls.countdown.textContent =
        `Calling ${CHECK_IN_PHONE_LABEL} in ${secondsLeft}...`;
      if (navigator.vibrate) navigator.vibrate(300);
      return;
    }

    clearInterval(okTimer);
    okTimer = null;
    timeRanOut();
  }, 1000);
}

function listNames(names) {
  if (names.length <= 2) return names.join(" and ");
  return `${names.slice(0, -1).join(", ")} and ${names[names.length - 1]}`;
}

function timeRanOut() {
  helpSent = true;
  okEls.countdown.textContent = "Alerting your friends...";

  // Alert friends first: opening the dialer can interrupt the page.
  api("/api/help", {})
    .then((data) => {
      okEls.help.textContent = data.notified.length
        ? `Alert sent to ${listNames(data.notified)}. They can see where you are.`
        : "No friends were alerted: you aren't sharing your location with anyone yet.";
    })
    .catch((helpError) => {
      okEls.help.textContent = `Couldn't alert your friends: ${helpError.message}`;
    })
    .finally(dialCheckInPhone);
}

function dialCheckInPhone() {
  // Browsers never place a call by themselves; the best a page can
  // do is open the dialer with the number filled in. Many phones
  // only allow that from a tap, so the Call button stays up in case
  // this automatic attempt is blocked.
  okEls.countdown.textContent =
    "Opening your phone's dialer. If it didn't open, tap the Call button.";
  window.location.href = `tel:${CHECK_IN_PHONE}`;
}

function hideOkCheck() {
  clearInterval(okTimer);
  okTimer = null;
  okEls.overlay.hidden = true;
}

function routeBackToParty() {
  const currentParty = () => {
    const party = latestData?.me?.party || latestData?.me?.leftParty;
    return party ? party.center : null;
  };

  if (!currentParty()) {
    showToast("Glad you're OK", "", "inside");
    return;
  }

  showToast("Glad you're OK", "Here's the way back to your party.", "inside");
  showRoute("your party", "inside", currentParty); // route.js
}

okEls.okButton.addEventListener("click", () => {
  hideOkCheck();

  if (helpSent) {
    // Tell friends it was a false alarm. helpSent stays true until
    // this finishes so a map refresh doesn't reopen the popup.
    api("/api/help/resolve", {})
      .then((data) => {
        helpSent = false;
        render(data); // map.js
      })
      .catch((resolveError) => {
        helpSent = false;
        showToast("Couldn't tell your friends you're OK", resolveError.message, "far");
      });
  }

  routeBackToParty();
});

okEls.callLink.addEventListener("click", () => {
  // The tap opens the dialer through the link itself.
  clearInterval(okTimer);
  okTimer = null;
});

// ==================================================
// A FRIEND NEEDS HELP
// ==================================================

const helpEls = {
  overlay: document.getElementById("helpAlert"),
  title: document.getElementById("helpAlertTitle"),
  detail: document.getElementById("helpAlertDetail"),
  route: document.getElementById("helpAlertRoute"),
  mapsLink: document.getElementById("helpAlertMaps"),
  close: document.getElementById("helpAlertClose"),
};

// Friends we've already alerted about their current help request.
const helpAlerted = new Set();
let helpAlertFriend = null;

function friendLocation(friendId) {
  return latestData?.friends.find((friend) => friend.id === friendId)?.location || null;
}

function routeToFriend(friend) {
  showRoute(friend.username, "far", () => friendLocation(friend.id)); // route.js
}

function checkHelpRequests(data) {
  // My own request is still open (e.g. the page was reloaded).
  if (data.me.needsHelp && !helpSent && okEls.overlay.hidden) {
    showOkCheck(data.me.distanceFromParty, { alreadySent: true });
  }

  data.friends.forEach((friend) => {
    if (friend.needsHelp && !helpAlerted.has(friend.id)) {
      helpAlerted.add(friend.id);
      showHelpAlert(friend);
    } else if (!friend.needsHelp && helpAlerted.has(friend.id)) {
      helpAlerted.delete(friend.id);
      if (helpAlertFriend?.id === friend.id) hideHelpAlert();
      const body = `${friend.username} answered their check-in.`;
      showToast(`${friend.username} is OK`, body, "inside");
      systemNotify(`${friend.username} is OK`, body, `friendsnme-help-${friend.id}`);
    }
  });
}

function showHelpAlert(friend) {
  helpAlertFriend = friend;
  helpEls.title.textContent = `${friend.username} needs help`;
  helpEls.detail.textContent = friend.location
    ? `${friend.username} didn't answer their “Are you OK?” check. ` +
      `Their location was updated ${timeAgo(friend.ageSeconds)}.`
    : `${friend.username} didn't answer their “Are you OK?” check, ` +
      "and we don't have their location.";

  helpEls.route.hidden = !friend.location;
  helpEls.mapsLink.hidden = !friend.location;
  if (friend.location) helpEls.mapsLink.href = googleMapsWalkingUrl(friend.location);

  helpEls.overlay.hidden = false;
  (friend.location ? helpEls.route : helpEls.close).focus();

  if (navigator.vibrate) navigator.vibrate([500, 200, 500, 200, 500]);
  systemNotify(
    `${friend.username} needs help`,
    `${friend.username} didn't answer their check-in. Open FriendsNMe to find them.`,
    `friendsnme-help-${friend.id}`,
    { requireInteraction: true }
  );
}

function hideHelpAlert() {
  helpEls.overlay.hidden = true;
  helpAlertFriend = null;
}

helpEls.route.addEventListener("click", () => {
  const friend = helpAlertFriend;
  hideHelpAlert();
  if (friend) routeToFriend(friend);
});

helpEls.close.addEventListener("click", hideHelpAlert);

// ==================================================
// NOTIFICATION PERMISSION
// ==================================================

function updateAlertsButton() {
  // Hidden once decided, or where notifications don't exist
  // (e.g. iPhone Safari unless the site is added to the home screen).
  alertEls.button.hidden = !canNotify || Notification.permission !== "default";
}

alertEls.button.addEventListener("click", async () => {
  const permission = await Notification.requestPermission();
  updateAlertsButton();
  if (permission === "granted") {
    showToast("Wandering alerts are on", "You'll get a notification when someone in your party wanders off.", "inside");
  }
});

if (canNotify && "serviceWorker" in navigator) {
  navigator.serviceWorker.register("/sw.js").catch((swError) => {
    console.error("Service worker registration failed:", swError);
  });
}

updateAlertsButton();
