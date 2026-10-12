/* wx-dlh hook script - the local proxy appends this to the WeChat Channels H5 page.
 *
 * It exists for one reason: decode_key never travels in a network request. It only
 * appears in the WeixinJSBridge JSAPI response, so the only way to see it is from inside
 * the page. Everything else here is spike instrumentation: the err_msg that marks a media
 * response is known on Windows (H5ExtTransfer:ok) and macOS (finderH5ExtTransfer:ok) but
 * not on Linux, so we report every invoke we see and let the operator read the log.
 *
 * decode_key is a 64-bit integer. It is pulled out of the raw response *text* with a
 * regex and kept as a string: JSON.parse turns an unquoted one into a float and silently
 * drops the low digits (13835058055282163712 -> 13835058055282164000), which produces a
 * completely different keystream and no error at all.
 *
 * Written as ES5 on purpose - it has to run in whatever webview the client ships.
 */
(function () {
  "use strict";

  var VERSION = "__WX_DLH_VERSION__";
  var CAPTURE_PATH = "__WX_DLH_CAPTURE_PATH__";
  var INCLUDE_SNIPPET = "__WX_DLH_INCLUDE_SNIPPET__";
  var SNIPPET_LIMIT = 8000;

  if (window.__wx_dlh) {
    return;
  }
  var state = { version: VERSION, invokes: 0, captures: 0, hooked: false };
  window.__wx_dlh = state;

  function log() {
    try {
      var args = ["[wx-dlh]"].concat(Array.prototype.slice.call(arguments));
      console.log.apply(console, args);
    } catch (e) {
      /* a webview without a console is not a reason to stop */
    }
  }

  function send(kind, payload) {
    var body;
    try {
      payload = payload || {};
      payload.kind = kind;
      payload.page_url = String(location.href);
      payload.user_agent = navigator.userAgent;
      payload.wx_dlh_version = VERSION;
      body = JSON.stringify(payload);
    } catch (e) {
      log("could not serialise", kind, e);
      return;
    }
    var url = location.origin + CAPTURE_PATH;
    try {
      // text/plain keeps this a "simple request": no CORS preflight, and it is
      // same-origin anyway because the proxy answers this path on every host.
      fetch(url, {
        method: "POST",
        body: body,
        headers: { "Content-Type": "text/plain;charset=UTF-8" },
        credentials: "omit",
        keepalive: true
      })["catch"](function (err) {
        log("fetch failed, falling back to sendBeacon", err);
        beacon(url, body);
      });
    } catch (e) {
      beacon(url, body);
    }
  }

  function beacon(url, body) {
    try {
      if (navigator.sendBeacon) {
        navigator.sendBeacon(url, new Blob([body], { type: "text/plain;charset=UTF-8" }));
      }
    } catch (e) {
      log("sendBeacon failed", e);
    }
  }

  function asText(value) {
    if (typeof value === "string") {
      return value;
    }
    try {
      return JSON.stringify(value);
    } catch (e) {
      return "";
    }
  }

  function keysOf(value) {
    try {
      return value && typeof value === "object" ? Object.keys(value) : [];
    } catch (e) {
      return [];
    }
  }

  /* Every "decode_key": <digits> in the raw text, in document order, digits verbatim.
   * The capture group around the quote tells us whether the JSON had it as a string or
   * as a bare number - one of the things M0 is supposed to find out. */
  function decodeKeys(text) {
    var re = /"decode_key"\s*:\s*("?)([0-9]{1,20})\1/g;
    var found = [];
    var match;
    while ((match = re.exec(text)) !== null) {
      found.push({ decode_key: match[2], decode_key_json_type: match[1] ? "string" : "number" });
    }
    return found;
  }

  /* Walk the parsed response for objects that look like a media entry. The reference
   * implementation reads object.object_desc.media[0]; walking instead survives the
   * shape drifting, which it does between clients. */
  function findMediaObjects(value, out, depth) {
    if (!value || typeof value !== "object" || depth > 8 || out.length > 16) {
      return out;
    }
    if (Object.prototype.hasOwnProperty.call(value, "decode_key")) {
      out.push(value);
    }
    var keys = keysOf(value);
    for (var i = 0; i < keys.length; i++) {
      findMediaObjects(value[keys[i]], out, depth + 1);
    }
    return out;
  }

  function mediaFrom(text) {
    var keys = decodeKeys(text);
    if (!keys.length) {
      return [];
    }
    var objects = [];
    try {
      objects = findMediaObjects(JSON.parse(text), [], 0);
    } catch (e) {
      log("decode_key found but the response is not parseable json", e);
    }
    var media = [];
    for (var i = 0; i < keys.length; i++) {
      var object = objects[i] || {};
      media.push({
        // never object.decode_key: by now JSON.parse has already ruined it
        decode_key: keys[i].decode_key,
        decode_key_json_type: keys[i].decode_key_json_type,
        url: object.url || "",
        url_token: object.url_token || "",
        file_size: object.file_size,
        description: object.description || "",
        nickname: object.nickname || ""
      });
    }
    return media;
  }

  function respText(res) {
    try {
      if (res && res.jsapi_resp) {
        var resp = res.jsapi_resp;
        return asText(resp.resp_json !== undefined ? resp.resp_json : resp);
      }
    } catch (e) {
      /* fall through to the whole response */
    }
    return asText(res);
  }

  function inspect(api, res) {
    state.invokes++;
    var errMsg = "";
    try {
      errMsg = (res && (res.err_msg || res.errMsg)) || "";
    } catch (e) {
      errMsg = "";
    }
    var text = respText(res);
    var media = text ? mediaFrom(text) : [];
    if (media.length) {
      state.captures += media.length;
      var payload = { api: api, err_msg: errMsg, media: media };
      if (INCLUDE_SNIPPET) {
        payload.resp_snippet = text.slice(0, SNIPPET_LIMIT);
      }
      log("captured", media.length, "media entry(ies) from", api, errMsg);
      send("capture", payload);
      return;
    }
    /* No media: report the shape only - api name, err_msg, top-level key names. No
     * values, so nothing about the account leaves the page. */
    send("invoke", { api: api, err_msg: errMsg, res_keys: keysOf(res) });
  }

  function wrapCallback(api, callback) {
    if (typeof callback !== "function") {
      return callback;
    }
    return function (res) {
      try {
        inspect(api, res);
      } catch (e) {
        log("inspect failed for", api, e);
      }
      return callback.apply(this, arguments);
    };
  }

  function hook(bridge) {
    if (!bridge || typeof bridge !== "object" || bridge.__wx_dlh_hooked) {
      return bridge;
    }
    var invoke = bridge.invoke;
    if (typeof invoke === "function") {
      bridge.invoke = function (api, params, callback) {
        return invoke.call(this, api, params, wrapCallback(api, callback));
      };
    }
    var on = bridge.on;
    if (typeof on === "function") {
      bridge.on = function (event, callback) {
        return on.call(this, event, wrapCallback("on:" + event, callback));
      };
    }
    bridge.__wx_dlh_hooked = true;
    state.hooked = true;
    log("hooked WeixinJSBridge", { invoke: typeof invoke, on: typeof on });
    send("hook", { has_invoke: typeof invoke === "function", has_on: typeof on === "function" });
    return bridge;
  }

  /* The bridge may not exist yet. Intercept the assignment so we wrap whichever object
   * the client installs, and keep polling as a fallback in case the property cannot be
   * redefined. A page that swaps out bridge.invoke *after* we wrapped it would shake the
   * hook off; no client is known to do that, and the probe/hook events in the log make it
   * visible if one does. */
  function install() {
    var current = window.WeixinJSBridge;
    try {
      Object.defineProperty(window, "WeixinJSBridge", {
        configurable: true,
        get: function () {
          return current;
        },
        set: function (value) {
          current = hook(value);
        }
      });
    } catch (e) {
      log("could not redefine window.WeixinJSBridge, polling instead", e);
      var tries = 0;
      var timer = setInterval(function () {
        tries++;
        if (window.WeixinJSBridge && window.WeixinJSBridge.invoke) {
          hook(window.WeixinJSBridge);
        }
        if (state.hooked || tries > 600) {
          clearInterval(timer);
        }
      }, 200);
    }
    if (current) {
      hook(current);
    }
    try {
      document.addEventListener("WeixinJSBridgeReady", function () {
        hook(window.WeixinJSBridge);
      });
    } catch (e) {
      /* not fatal */
    }
  }

  install();
  send("probe", {
    has_bridge: !!window.WeixinJSBridge,
    hooked: state.hooked,
    ready_state: document.readyState,
    in_iframe: window.top !== window.self,
    referrer: document.referrer || ""
  });
  log("ready", VERSION, "capture ->", location.origin + CAPTURE_PATH);
})();
