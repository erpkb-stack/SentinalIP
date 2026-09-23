/* Sentinel IP AI - API client
   One place that knows how to talk to the backend: CSRF header, consistent
   error envelope, session-expiry handling. */
(function (global) {
  "use strict";

  function readCookie(name) {
    return document.cookie
      .split("; ")
      .map(function (c) { return c.split("="); })
      .filter(function (p) { return p[0] === name; })
      .map(function (p) { return decodeURIComponent(p.slice(1).join("=")); })[0] || null;
  }

  function ApiError(payload, status) {
    var err = new Error((payload && payload.message) || "Request failed.");
    err.name = "ApiError";
    err.code = (payload && payload.code) || "HTTP_" + status;
    err.status = status;
    err.details = (payload && payload.details) || null;
    return err;
  }

  // A fetch that is cancelled because the user navigated away is not a failure.
  // Without this, leaving a page mid-load pops a spurious "connection" toast.
  var navigatingAway = false;
  ["beforeunload", "pagehide"].forEach(function (evt) {
    window.addEventListener(evt, function () { navigatingAway = true; });
  });

  function fieldErrors(err) {
    var out = {};
    if (err && err.details && Array.isArray(err.details.fields)) {
      err.details.fields.forEach(function (f) {
        var key = String(f.field || "").split(".").pop();
        if (!out[key]) out[key] = f.message;
      });
    }
    return out;
  }

  //: A request that never returns must fail loudly rather than spin forever.
  //: Reverse proxies and tunnels can hold a connection open indefinitely.
  var DEFAULT_TIMEOUT_MS = 20000;

  async function request(method, url, options) {
    options = options || {};
    var headers = Object.assign({}, options.headers || {});
    var body = options.body;

    if (!(body instanceof FormData) && body !== undefined && body !== null) {
      headers["Content-Type"] = "application/json";
      body = JSON.stringify(body);
    }
    if (["POST", "PUT", "PATCH", "DELETE"].indexOf(method) !== -1) {
      var token = readCookie("sentinel_csrf");
      if (token) headers["X-CSRF-Token"] = token;
    }

    var controller = typeof AbortController !== "undefined"
      ? new AbortController() : null;
    var timedOut = false;
    var timeoutMs = options.timeout || DEFAULT_TIMEOUT_MS;
    var timer = controller ? setTimeout(function () {
      timedOut = true;
      controller.abort();
    }, timeoutMs) : null;

    var response;
    try {
      response = await fetch(url, {
        method: method,
        headers: headers,
        body: body,
        credentials: "same-origin",
        signal: controller ? controller.signal : undefined,
      });
    } catch (networkError) {
      if (navigatingAway) {
        throw ApiError(
          { code: "NAVIGATION_ABORTED", message: "Request cancelled." }, 0
        );
      }
      if (timedOut) {
        throw ApiError(
          {
            code: "REQUEST_TIMEOUT",
            message: "The server did not respond within " +
                     Math.round(timeoutMs / 1000) + " seconds.",
            details: { url: url, method: method },
          },
          0
        );
      }
      throw ApiError(
        {
          code: "NETWORK_ERROR",
          message: "Could not reach the server. Check your connection.",
          details: { url: url, method: method },
        },
        0
      );
    } finally {
      if (timer) clearTimeout(timer);
    }

    if (response.status === 204) return null;

    var payload = null;
    var contentType = response.headers.get("content-type") || "";
    if (contentType.indexOf("application/json") !== -1) {
      try { payload = await response.json(); } catch (e) { payload = null; }
    }

    if (!response.ok) {
      var errBody = (payload && payload.error) || null;
      if (response.status === 401) {
        var reason = (errBody && errBody.code) || "not_authenticated";
        if (!/\/login$/.test(location.pathname)) {
          location.href = "/login?next=" + encodeURIComponent(
            location.pathname + location.search
          ) + "&reason=" + reason.toLowerCase();
        }
      }
      throw ApiError(errBody, response.status);
    }
    return payload;
  }

  function qs(params) {
    var search = new URLSearchParams();
    Object.keys(params || {}).forEach(function (key) {
      var value = params[key];
      if (value === undefined || value === null || value === "") return;
      if (Array.isArray(value)) {
        value.forEach(function (v) { if (v !== "" && v != null) search.append(key, v); });
      } else {
        search.append(key, value);
      }
    });
    var s = search.toString();
    return s ? "?" + s : "";
  }

  var Api = {
    ApiError: ApiError,
    fieldErrors: fieldErrors,
    qs: qs,
    readCookie: readCookie,
    get: function (url, params) { return request("GET", url + qs(params)); },
    post: function (url, body) { return request("POST", url, { body: body }); },
    put: function (url, body) { return request("PUT", url, { body: body }); },
    del: function (url) { return request("DELETE", url); },
    upload: function (url, formData) { return request("POST", url, { body: formData }); },

    // ---- domain helpers ----
    me: function () { return Api.get("/api/auth/me"); },
    meta: function () { return Api.get("/api/meta"); },
    logout: function () { return Api.post("/api/auth/logout", {}); },
    dashboard: function (params) { return Api.get("/api/dashboard", params); },
    cases: function (params) { return Api.get("/api/cases", params); },
    caseDetail: function (id, opts) {
      return Api.get("/api/cases/" + id, (opts && opts.poll) ? { poll: true } : null);
    },
    createCase: function (body) { return Api.post("/api/cases", body); },
    analyze: function (id) { return Api.post("/api/cases/" + id + "/analyze", {}); },
    agentRun: function (caseId, runId) {
      return Api.get("/api/cases/" + caseId + "/agents/" + runId);
    },
    approve: function (id, body) { return Api.post("/api/cases/" + id + "/approve", body || {}); },
    reject: function (id, body) { return Api.post("/api/cases/" + id + "/reject", body || {}); },
    requestChanges: function (id, body) {
      return Api.post("/api/cases/" + id + "/request-changes", body || {});
    },
    fileEnforcement: function (id) { return Api.post("/api/cases/" + id + "/file", {}); },
    marketplaceResponse: function (id) {
      return Api.post("/api/cases/" + id + "/enforcement/response", {});
    },
    caseAudit: function (id) { return Api.get("/api/cases/" + id + "/audit"); },
    evidence: function (params) { return Api.get("/api/evidence", params); },
    reviewEvidence: function (id, body) {
      return Api.put("/api/evidence/" + id + "/review", body);
    },
    vendors: function (params) { return Api.get("/api/vendors", params); },
    vendorOptions: function () { return Api.get("/api/vendors/options"); },
    users: function (params) { return Api.get("/api/users", params); },
    roles: function () { return Api.get("/api/users/roles"); },
    marketplaces: function () { return Api.get("/api/admin/marketplaces"); },
    slaPolicies: function () { return Api.get("/api/admin/sla-policies"); },
    autonomy: function () { return Api.get("/api/admin/autonomy"); },
    autonomyPolicy: function () { return Api.get("/api/admin/autonomy/policy"); },
    permissions: function () { return Api.get("/api/admin/permissions"); },
    audit: function (params) { return Api.get("/api/audit", params); },
    aiOps: function () { return Api.get("/api/agents"); },
    agentRuns: function (params) { return Api.get("/api/agents/runs", params); },
    notifications: function (params) { return Api.get("/api/notifications", params); },
    notificationCount: function () { return Api.get("/api/notifications/count"); },
    markRead: function (id) { return Api.post("/api/notifications/" + id + "/read", {}); },
    markAllRead: function () { return Api.post("/api/notifications/read-all", {}); },
    enforcement: function (params) { return Api.get("/api/enforcement", params); },
    reports: function (params) { return Api.get("/api/reports/summary", params); },
  };

  global.Api = Api;
})(window);
