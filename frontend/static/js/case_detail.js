(function () {
  "use strict";

  var root = document.getElementById("caseRoot");
  var caseId = Number(root.dataset.caseId);
  var current = null;
  var me = null;
  var socket = null;
  var pollTimer = null;

  var AGENT_STATUS_LABEL = {
    PENDING: "Pending", RUNNING: "Running", COMPLETE: "Complete",
    AWAITING_APPROVAL: "Awaiting approval", FAILED: "Failed", SKIPPED: "Skipped",
  };

  /* ================================================================== */
  /* Rendering                                                           */
  /* ================================================================== */
  function renderHeader(c) {
    document.getElementById("caseNumber").textContent = c.case_number;
    document.getElementById("caseTitle").textContent = c.title;
    document.title = c.case_number + " · Sentinel IP AI";

    var band = (c.risk_level || UI.riskBand(c.ai_confidence)).toLowerCase();
    var ringColor = {
      low: "#6B7280", medium: "#D97706", high: "#BF5700", critical: "#DC2626",
    }[band] || "#BF5700";
    var pct = c.ai_confidence === null || c.ai_confidence === undefined
      ? 0 : Math.round(c.ai_confidence);

    document.getElementById("confidenceHero").innerHTML =
      '<div class="confidence-ring" style="--pct:' + pct + ';--ring-color:' + ringColor + '">' +
        '<div class="confidence-ring__inner">' +
          (c.ai_confidence === null || c.ai_confidence === undefined ? "—" : pct + "%") +
        "</div></div>" +
      "<div>" +
        '<div class="stat__label">AI Confidence</div>' +
        '<div style="font-size:1.05rem;font-weight:700;color:' + ringColor + '">' +
          UI.esc(c.risk_level ? UI.titleize(c.risk_level) : "Not analysed") + "</div>" +
        '<div class="stat__hint">' + UI.esc(c.confidence_disclaimer || "") + "</div>" +
      "</div>";

    document.getElementById("statusCard").innerHTML =
      '<div class="stat__label">Status</div>' +
      '<div style="margin-top:.375rem">' + UI.badge(c.status) + "</div>" +
      '<div class="stat__hint">' +
        (c.recommended_action_label
          ? "AI recommends " + UI.esc(c.recommended_action_label)
          : "No recommendation yet") + "</div>";

    var sla = c.sla || {};
    document.getElementById("slaCard").innerHTML =
      '<div class="stat__label">SLA</div>' +
      '<div style="margin-top:.375rem">' + UI.slaCell(sla) + "</div>" +
      '<div class="stat__hint">Priority: ' + UI.esc(UI.titleize(c.priority)) + "</div>";

    var actions = [];
    if (["DRAFT", "CHANGES_REQUESTED", "REJECTED"].indexOf(c.status) !== -1) {
      actions.push('<button class="btn btn--primary" id="runAnalysisBtn">Run AI Analysis</button>');
    }
    if (c.status === "SUBMITTED" || c.status === "UNDER_REVIEW") {
      actions.push('<button class="btn" id="responseBtn">Simulate marketplace response</button>');
    }
    actions.push('<button class="btn btn--ghost" id="refreshBtn">Refresh</button>');
    document.getElementById("caseActions").innerHTML = actions.join("");
    wireHeaderActions();
  }

  function renderOverview(c) {
    var listing = c.listing || {};
    var product = c.product || {};
    var rows = [
      ["Vendor", c.vendor_name],
      ["Product", product.name || c.product_name],
      ["SKU", product.sku],
      ["Trademark", product.trademark],
      ["Marketplace", c.marketplace],
      ["Seller", listing.seller_name],
      ["Seller country", listing.seller_country],
      ["Listing price", listing.price != null
        ? UI.fmtMoney(listing.price, listing.currency) : null],
      ["Expected price", product.msrp != null
        ? UI.fmtMoney(product.msrp, product.currency) : null],
      ["Infringement", c.infringement_label],
      ["Assigned to", c.assigned_to_name],
      ["Created by", c.created_by_name],
      ["Created", UI.fmtDateTime(c.created_at)],
      ["SLA deadline", c.sla && c.sla.deadline ? UI.fmtDateTime(c.sla.deadline) : null],
    ];
    var html = rows.filter(function (r) { return r[1]; }).map(function (r) {
      return "<div><dt>" + UI.esc(r[0]) + "</dt><dd>" + UI.esc(r[1]) + "</dd></div>";
    }).join("");
    if (listing.url) {
      html += '<div style="grid-column:1/-1"><dt>Listing URL</dt><dd>' +
        '<a href="' + UI.safeUrl(listing.url) + '" target="_blank" rel="noopener noreferrer nofollow" ' +
        'class="truncate" style="display:block">' + UI.esc(listing.url) + "</a></dd></div>";
    }
    document.getElementById("overview").innerHTML = html;
  }

  function renderPipeline(c) {
    var el = document.getElementById("pipeline");
    el.innerHTML = c.agents.map(function (a) {
      var status = String(a.status || "PENDING");
      var meta = [];
      if (a.started_at) meta.push(UI.fmtDateTime(a.started_at));
      if (a.duration_ms != null) meta.push(UI.fmtDuration(a.duration_ms));
      if (a.evidence_count) meta.push(a.evidence_count + " evidence");
      return '<button type="button" class="agent-card agent-card--' +
          UI.esc(status.toLowerCase()) + '" data-run="' + (a.run_id || "") +
          '" ' + (a.run_id ? "" : "disabled") + '>' +
        '<span class="agent-card__seq">' + a.sequence + "</span>" +
        '<div class="agent-card__name">' + UI.esc(a.label) + "</div>" +
        UI.badge(status, AGENT_STATUS_LABEL[status] || UI.titleize(status)) +
        (a.confidence != null
          ? '<div class="agent-card__meta" style="margin-top:.375rem">Confidence ' +
            Math.round(a.confidence) + "%</div>" : "") +
        '<div class="agent-card__meta">' + UI.esc(meta.join(" · ")) + "</div>" +
        (a.summary ? '<div class="agent-card__summary">' + UI.esc(a.summary) + "</div>" : "") +
        (a.error ? '<div class="agent-card__summary" style="color:var(--error)">' +
          UI.esc(a.error) + "</div>" : "") +
      "</button>";
    }).join("");

    el.querySelectorAll(".agent-card[data-run]").forEach(function (btn) {
      if (!btn.dataset.run) return;
      btn.addEventListener("click", function () { openAgentRun(btn.dataset.run); });
    });

    var done = c.agents.filter(function (a) {
      return a.status === "COMPLETE" || a.status === "AWAITING_APPROVAL";
    }).length;
    document.getElementById("pipelineHint").textContent =
      done + " of " + c.agents.length + " agents finished";
  }

  function findingList(items, kind) {
    if (!items || !items.length) return "";
    var marker = { RISK: "!", CONTRADICTORY: "−", SUPPORTING: "•", ALTERNATIVE: "→" }[kind] || "•";
    var cls = kind === "RISK" ? " reason-list--risk"
            : kind === "CONTRADICTORY" ? " reason-list--contra" : "";
    return '<ul class="reason-list' + cls + '">' + items.map(function (f) {
      return "<li><span class=\"marker\">" + marker + "</span><span>" +
        "<strong>" + UI.esc(f.title) + "</strong>" +
        (f.detail ? '<div class="card__hint">' + UI.esc(f.detail) + "</div>" : "") +
        (f.evidence_refs && f.evidence_refs.length
          ? '<div class="evidence-refs">' + f.evidence_refs.map(function (r) {
              return "<code>" + UI.esc(r) + "</code>";
            }).join("") + "</div>" : "") +
        "</span></li>";
    }).join("") + "</ul>";
  }

  function renderDecision(c) {
    var card = document.getElementById("decisionCard");
    var enf = c.enforcement;
    if (c.ai_confidence === null || c.ai_confidence === undefined) {
      card.hidden = true;
      return;
    }
    card.hidden = false;

    var band = (c.risk_level || "").toLowerCase();
    var supporting = c.findings && c.findings.SUPPORTING;
    var contradictory = c.findings && c.findings.CONTRADICTORY;
    var risks = c.findings && c.findings.RISK;
    var alternatives = c.findings && c.findings.ALTERNATIVE;

    var html =
      "<h3>Verification Result</h3>" +
      '<div style="display:flex;gap:1rem;align-items:center;flex-wrap:wrap;margin:.5rem 0 .875rem">' +
        UI.confidenceCell(c.ai_confidence, c.risk_level) +
        UI.badge(c.risk_level || "LOW", "Risk: " + UI.titleize(c.risk_level || "low")) +
      "</div>" +
      (c.ai_summary ? "<p><strong>Finding.</strong> " + UI.esc(c.ai_summary) + "</p>" : "") +
      (supporting && supporting.length
        ? '<h4 style="margin:1rem 0 .375rem;font-size:.85rem">Supporting evidence</h4>' +
          findingList(supporting, "SUPPORTING") : "") +
      (contradictory && contradictory.length
        ? '<h4 style="margin:1rem 0 .375rem;font-size:.85rem">Contradictory evidence</h4>' +
          findingList(contradictory, "CONTRADICTORY")
        : '<p class="card__hint" style="margin-top:.75rem">No contradictory evidence was recorded.</p>');

    if (enf) {
      html +=
        '<div class="recommendation" style="margin-top:1.25rem">' +
          '<div class="recommendation__head">' +
            '<div class="stat__label">Recommended Enforcement</div>' +
            '<div class="recommendation__action">' + UI.esc(enf.action_label) + "</div>" +
            '<div style="margin-top:.375rem;display:flex;gap:.5rem;flex-wrap:wrap">' +
              UI.badge(c.risk_level || "MEDIUM",
                       "AI recommendation · " + UI.titleize(c.risk_level || "medium") + " confidence") +
              UI.badge(enf.status) +
              (enf.is_simulated ? '<span class="demo-tag">DEMO / SIMULATED</span>' : "") +
            "</div>" +
          "</div>" +
          '<div class="card__body">' +
            "<h4 style=\"font-size:.85rem;margin-bottom:.375rem\">Reasoning</h4>" +
            "<p>" + UI.esc(enf.reasoning || "") + "</p>" +
            (risks && risks.length
              ? '<h4 style="margin:1rem 0 .375rem;font-size:.85rem">Risks of taking this action</h4>' +
                findingList(risks, "RISK") : "") +
            (alternatives && alternatives.length
              ? '<h4 style="margin:1rem 0 .375rem;font-size:.85rem">Alternatives considered</h4>' +
                findingList(alternatives, "ALTERNATIVE") : "") +
            (enf.supporting_evidence && enf.supporting_evidence.length
              ? '<h4 style="margin:1rem 0 .375rem;font-size:.85rem">Evidence referenced</h4>' +
                '<div class="evidence-refs">' + enf.supporting_evidence.map(function (r) {
                  return "<code>" + UI.esc(r) + "</code>";
                }).join("") + "</div>" : "") +
            (enf.notice_draft
              ? '<h4 style="margin:1.25rem 0 .375rem;font-size:.85rem">Notice draft ' +
                '<span class="card__hint">(prepared, not sent)</span></h4>' +
                '<pre class="notice-draft">' + UI.esc(enf.notice_draft) + "</pre>" : "") +
          "</div>" +
        "</div>";
    }

    document.getElementById("decisionBody").innerHTML = html;
    var providerAgent = (c.agents || []).find(function (a) { return a.provider; });
    document.getElementById("decisionProvider").textContent =
      providerAgent ? "Provider: " + providerAgent.provider : "";
  }

  function renderGate(c) {
    var slot = document.getElementById("gateSlot");
    var enf = c.enforcement;
    if (!enf) { slot.innerHTML = ""; return; }

    if (enf.status === "AWAITING_APPROVAL") {
      var canApprove = me && (me.permissions || []).indexOf("case:approve") !== -1;
      slot.innerHTML =
        '<div class="approval-gate">' +
          '<div class="approval-gate__label">⚠ Human approval required</div>' +
          '<div class="approval-gate__rec">AI recommendation: ' +
            UI.esc(enf.action_label) + "</div>" +
          '<div class="approval-gate__why">' + UI.esc(enf.reasoning || "") + "</div>" +
          (canApprove
            ? '<div class="approval-gate__actions">' +
                '<button class="btn btn--success" data-decide="approve">Approve</button>' +
                '<button class="btn btn--danger" data-decide="reject">Reject</button>' +
                '<button class="btn" data-decide="changes">Request Changes</button>' +
              "</div>"
            : '<p class="card__hint" style="margin:0">Your role cannot decide on this ' +
              "recommendation. Contact an administrator.</p>") +
        "</div>";
      slot.querySelectorAll("[data-decide]").forEach(function (btn) {
        btn.addEventListener("click", function () { openDecision(btn.dataset.decide, enf); });
      });
      return;
    }

    if (enf.has_valid_approval) {
      slot.innerHTML =
        '<div class="approval-gate approval-gate--cleared">' +
          '<div class="approval-gate__label">✓ Human approval on record</div>' +
          '<div class="approval-gate__rec">' + UI.esc(enf.action_label) + "</div>" +
          '<div class="approval-gate__why">' +
            (enf.submitted_at
              ? "Filed " + UI.esc(UI.fmtDateTime(enf.submitted_at)) +
                (enf.submission_reference ? " · " + UI.esc(enf.submission_reference) : "")
              : "Approved and cleared for filing.") +
            (enf.is_simulated ? " (DEMO / SIMULATED — no external system was contacted.)" : "") +
          "</div>" +
        "</div>";
      return;
    }

    if (enf.status === "REJECTED") {
      slot.innerHTML =
        '<div class="banner banner--danger"><span>✕</span><span>' +
        "This recommendation was rejected by a human reviewer. " +
        "Re-run analysis to generate a new recommendation.</span></div>";
      return;
    }
    slot.innerHTML = "";
  }

  function renderTimeline(c) {
    var el = document.getElementById("enforcementTimeline");
    var steps = c.enforcement_timeline || [];
    if (!steps.length) {
      el.innerHTML = '<li class="card__hint">No enforcement action yet.</li>';
      return;
    }
    var firstPending = steps.findIndex(function (s) { return !s.done; });
    el.innerHTML = steps.map(function (s, i) {
      var cls = s.done ? "is-done" : (i === firstPending ? "is-current" : "");
      return '<li class="' + cls + '"><span class="timeline__dot"></span>' +
        '<div class="timeline__title">' + UI.esc(s.label) + "</div>" +
        (s.at ? '<div class="timeline__meta">' + UI.esc(UI.fmtDateTime(s.at)) + "</div>" : "") +
        (s.detail ? '<div class="timeline__detail">' + UI.esc(s.detail) + "</div>" : "") +
        "</li>";
    }).join("");

    var foot = document.getElementById("enforcementFoot");
    if (c.enforcement && c.enforcement.marketplace_response) {
      foot.hidden = false;
      foot.innerHTML = '<div class="card__hint"><strong>Marketplace response.</strong> ' +
        UI.esc(c.enforcement.marketplace_response) + "</div>";
    } else { foot.hidden = true; }
  }

  function renderEvidence(c) {
    var panel = document.getElementById("evidencePanel");
    var items = c.evidence || [];
    if (!items.length) {
      panel.innerHTML = UI.emptyState("No evidence yet",
        "Run the AI pipeline or upload evidence to build the record.");
      return;
    }
    var groups = {};
    items.forEach(function (e) {
      (groups[e.category_label || e.category] =
        groups[e.category_label || e.category] || []).push(e);
    });

    panel.innerHTML = Object.keys(groups).map(function (label) {
      var rows = groups[label].map(function (e) {
        return '<div class="evidence-item' + (e.is_archived ? " is-archived" : "") +
            '" data-evidence="' + e.id + '">' +
          '<div class="evidence-item__top">' +
            '<span class="evidence-item__ref">' + UI.esc(e.evidence_ref) + "</span>" +
            '<span class="evidence-item__title">' + UI.esc(e.title) + "</span>" +
            UI.badge(e.relevance) +
            (e.is_archived ? UI.badge("INACTIVE", "Archived") : "") +
          "</div>" +
          (e.description ? '<div class="evidence-item__desc">' +
            UI.esc(e.description) + "</div>" : "") +
          '<div class="evidence-item__meta">' +
            "<span>Type: " + UI.esc(UI.titleize(e.evidence_type)) + "</span>" +
            "<span>Collected by: " + UI.esc(e.collected_by) + "</span>" +
            "<span>" + UI.esc(UI.fmtDateTime(e.collected_at)) + "</span>" +
            (e.file_size ? "<span>" + UI.esc(UI.fmtBytes(e.file_size)) + "</span>" : "") +
            (e.checksum ? '<span title="' + UI.esc(e.checksum) + '">SHA-256 ' +
              UI.esc(e.checksum.slice(0, 12)) + "…</span>" : "") +
            '<span style="display:flex;align-items:center;gap:.375rem">Strength ' +
              '<span class="strength-bar"><span style="width:' +
              Math.max(0, Math.min(100, e.strength)) + '%"></span></span>' +
              e.strength + "</span>" +
          "</div>" +
          '<div class="evidence-item__actions">' +
            (e.source_url ? '<a class="btn btn--sm" target="_blank" rel="noopener noreferrer nofollow" href="' +
              UI.safeUrl(e.source_url) + '">Open source</a>' : "") +
            (e.download_url ? '<a class="btn btn--sm" href="' + UI.safeUrl(e.download_url) +
              '">Download</a>' : "") +
            '<button class="btn btn--sm" data-mark="RELEVANT" data-id="' + e.id + '">Relevant</button>' +
            '<button class="btn btn--sm" data-mark="NOT_RELEVANT" data-id="' + e.id + '">Not relevant</button>' +
            '<button class="btn btn--sm" data-mark="DISPUTED" data-id="' + e.id + '">Disputed</button>' +
          "</div>" +
        "</div>";
      }).join("");
      return '<div class="evidence-group"><div class="evidence-group__head">' +
        UI.esc(label) + " · " + groups[label].length + "</div>" + rows + "</div>";
    }).join("");

    panel.querySelectorAll("[data-mark]").forEach(function (btn) {
      btn.addEventListener("click", async function () {
        UI.setLoading(btn, true, "Saving");
        try {
          await Api.reviewEvidence(btn.dataset.id, { relevance: btn.dataset.mark });
          UI.toast.success("Evidence marked " + UI.titleize(btn.dataset.mark) + ".");
          await load(true);
        } catch (err) {
          UI.setLoading(btn, false);
          UI.handleError(err, "Could not update the evidence.");
        }
      });
    });
  }

  function renderApprovals(c) {
    var el = document.getElementById("approvalHistory");
    var items = c.approvals || [];
    if (!items.length) {
      el.innerHTML = '<p class="card__hint" style="margin:0">' +
        "No human decision has been recorded yet.</p>";
      return;
    }
    el.innerHTML = '<ul class="feed">' + items.map(function (a) {
      var tone = a.decision === "APPROVED" ? "success"
               : a.decision === "REJECTED" ? "danger" : "warning";
      return "<li>" +
        '<div class="feed__icon">' + UI.esc(UI.initials(a.decided_by_name || a.decided_by_email)) + "</div>" +
        '<div class="feed__body">' +
          '<div class="feed__title">' + UI.badge(a.decision, UI.titleize(a.decision), tone) +
            " " + UI.esc(a.decided_by_name || a.decided_by_email) + "</div>" +
          '<div class="feed__meta">' + UI.esc(UI.fmtDateTime(a.decided_at)) +
            " · " + UI.esc(UI.titleize(a.decided_by_role)) +
            " · v" + a.recommendation_version +
            (a.evidence_item_count != null
              ? " · " + a.evidence_item_count + " evidence items frozen" : "") + "</div>" +
          (a.comments ? '<div class="feed__meta">“' + UI.esc(a.comments) + "”</div>" : "") +
          (a.reason ? '<div class="feed__meta">Reason: ' + UI.esc(a.reason) + "</div>" : "") +
        "</div></li>";
    }).join("") + "</ul>";
  }

  function renderAutonomy(c) {
    var a = c.autonomy || {};
    document.getElementById("autonomyPanel").innerHTML =
      '<span class="pill-tier">' + UI.esc(a.label || "Tier 1") + "</span>" +
      '<p class="card__hint" style="margin:.625rem 0 0">' + UI.esc(a.description || "") + "</p>" +
      '<p class="card__hint" style="margin:.5rem 0 0"><strong>Human approval: ' +
        (a.requires_human_approval === false ? "not required for this action class"
                                             : "required") + ".</strong></p>";
  }

  async function renderActivity() {
    var el = document.getElementById("activityPanel");
    try {
      var rows = await Api.caseAudit(caseId);
      if (!rows.length) {
        el.innerHTML = '<p class="card__hint" style="margin:0">No activity recorded yet.</p>';
        return;
      }
      el.innerHTML = '<ul class="feed">' + rows.slice(0, 30).map(function (r) {
        return "<li>" +
          '<div class="feed__icon">' + UI.esc((r.action || "?").charAt(0)) + "</div>" +
          '<div class="feed__body">' +
            '<div class="feed__title">' + UI.esc(UI.titleize(r.action)) +
              (r.success ? "" : ' <span class="badge badge--danger">denied</span>') + "</div>" +
            '<div class="feed__meta">' + UI.esc(r.user_email || "system") + " · " +
              UI.esc(UI.fmtDateTime(r.timestamp)) + "</div>" +
            (r.detail ? '<div class="feed__meta">' + UI.esc(r.detail) + "</div>" : "") +
          "</div></li>";
      }).join("") + "</ul>";
    } catch (err) {
      el.innerHTML = '<p class="card__hint" style="margin:0">Activity unavailable.</p>';
    }
  }

  /* ================================================================== */
  /* Interactions                                                        */
  /* ================================================================== */
  function wireHeaderActions() {
    var runBtn = document.getElementById("runAnalysisBtn");
    if (runBtn) {
      runBtn.addEventListener("click", async function () {
        UI.setLoading(runBtn, true, "Starting");
        try {
          await Api.analyze(caseId);
          UI.toast("AI analysis started. The pipeline updates live.", "info");
          startPolling();
        } catch (err) {
          UI.setLoading(runBtn, false);
          UI.handleError(err, "Could not start the analysis.");
        }
      });
    }
    var respBtn = document.getElementById("responseBtn");
    if (respBtn) {
      respBtn.addEventListener("click", async function () {
        var ok = await UI.confirm({
          title: "Simulate a marketplace response?",
          message: "This is a DEMO action. It records a simulated outcome for this filing " +
                   "so you can see the tracking states.",
          confirmLabel: "Simulate response", tone: "primary",
        });
        if (!ok) return;
        UI.setLoading(respBtn, true, "Recording");
        try {
          await Api.marketplaceResponse(caseId);
          UI.toast.success("Marketplace response recorded.");
          await load(true);
        } catch (err) {
          UI.setLoading(respBtn, false);
          UI.handleError(err);
        }
      });
    }
    var refreshBtn = document.getElementById("refreshBtn");
    if (refreshBtn) {
      refreshBtn.addEventListener("click", function () { load(true); });
    }
  }

  document.getElementById("addEvidenceBtn").addEventListener("click", function () {
    var panel = UI.openModal({
      title: "Add evidence",
      body:
        '<div class="form-grid">' +
          '<div class="field field--full"><label for="evCategory">Category</label>' +
            '<select class="select" id="evCategory"></select></div>' +
          '<div class="field field--full"><label for="evTitle">Title</label>' +
            '<input class="input" id="evTitle" placeholder="What this evidence shows"></div>' +
          '<div class="field field--full"><label for="evFile">File</label>' +
            '<input class="input" type="file" id="evFile">' +
            "<small id=\"evHint\"></small></div>" +
          '<div class="field field--full"><label for="evUrl">…or a URL</label>' +
            '<input class="input" type="url" id="evUrl" placeholder="https://…"></div>' +
          '<div class="field field--full"><label for="evDesc">Description</label>' +
            '<textarea class="textarea" id="evDesc"></textarea></div>' +
        "</div>",
      footer: '<button class="btn" data-modal-close>Cancel</button>' +
              '<button class="btn btn--primary" id="evSubmit">Add evidence</button>',
    });

    Api.meta().then(function (meta) {
      panel.querySelector("#evCategory").innerHTML = meta.evidence_categories
        .map(function (c) {
          return '<option value="' + UI.esc(c.value) + '">' + UI.esc(c.label) + "</option>";
        }).join("");
      panel.querySelector("#evHint").textContent =
        "Accepted: " + meta.upload.extensions.join(", ") +
        " · up to " + meta.upload.max_mb + " MB";
      panel.querySelector("#evFile").setAttribute("accept", meta.upload.extensions.join(","));
    }).catch(function () {});

    panel.querySelector("#evSubmit").addEventListener("click", async function () {
      var btn = panel.querySelector("#evSubmit");
      var file = panel.querySelector("#evFile").files[0];
      var url = panel.querySelector("#evUrl").value.trim();
      var title = panel.querySelector("#evTitle").value.trim();
      var category = panel.querySelector("#evCategory").value;
      var description = panel.querySelector("#evDesc").value.trim();

      if (!file && !url) { UI.toast("Choose a file or enter a URL.", "warning"); return; }
      UI.setLoading(btn, true, "Uploading");
      try {
        if (file) {
          var fd = new FormData();
          fd.append("file", file);
          fd.append("category", category);
          if (title) fd.append("title", title);
          if (description) fd.append("description", description);
          await Api.upload("/api/cases/" + caseId + "/evidence", fd);
        }
        if (url) {
          await Api.post("/api/cases/" + caseId + "/evidence/url", {
            category: category, title: title || "External reference",
            url: url, description: description,
          });
        }
        UI.closeModal();
        UI.toast.success("Evidence added.");
        await load(true);
      } catch (err) {
        UI.setLoading(btn, false);
        UI.handleError(err, "The evidence could not be added.");
      }
    });
  });

  function openDecision(kind, enf) {
    var config = {
      approve: {
        title: "Approve this recommendation?",
        intro: "You are authorizing <strong>" + UI.esc(enf.action_label) +
               "</strong>. Your name, the AI recommendation, its confidence and a " +
               "snapshot of the evidence are recorded permanently.",
        label: "Comments (optional)", required: false,
        button: "Approve", tone: "success", call: Api.approve, field: "comments",
      },
      reject: {
        title: "Reject this recommendation?",
        intro: "The case will close as rejected. A reason is required.",
        label: "Reason", required: true,
        button: "Reject", tone: "danger", call: Api.reject, field: "reason",
      },
      changes: {
        title: "Request changes?",
        intro: "The recommendation returns to draft for revision. Comments are required.",
        label: "What should change?", required: true,
        button: "Request changes", tone: "primary",
        call: Api.requestChanges, field: "comments",
      },
    }[kind];

    var panel = UI.openModal({
      title: config.title,
      body:
        "<p>" + config.intro + "</p>" +
        '<div class="banner banner--accent" style="margin:.875rem 0">' +
          "<span>ℹ</span><span>AI confidence is a model score, not a legal " +
          "determination. You are the decision-maker of record.</span></div>" +
        '<div class="field"><label for="decisionText">' + UI.esc(config.label) +
          (config.required ? '<span class="req">*</span>' : "") + "</label>" +
          '<textarea class="textarea" id="decisionText"></textarea>' +
          '<div class="field__error" hidden></div></div>',
      footer: '<button class="btn" data-modal-close>Cancel</button>' +
              '<button class="btn btn--' + config.tone + '" id="decisionSubmit">' +
              UI.esc(config.button) + "</button>",
    });

    panel.querySelector("#decisionSubmit").addEventListener("click", async function () {
      var btn = panel.querySelector("#decisionSubmit");
      var text = panel.querySelector("#decisionText").value.trim();
      if (config.required && !text) {
        var field = panel.querySelector(".field");
        field.classList.add("has-error");
        var err = field.querySelector(".field__error");
        err.textContent = "This is required."; err.hidden = false;
        return;
      }
      var body = { enforcement_action_id: enf.id };
      body[config.field] = text || null;

      UI.setLoading(btn, true, "Recording");
      try {
        var res = await config.call(caseId, body);
        UI.closeModal();
        UI.toast.success(res.message || "Decision recorded.");
        if (res.filing) UI.toast(res.filing, "info", 6000);
        await load(true);
      } catch (e) {
        UI.setLoading(btn, false);
        UI.handleError(e, "The decision could not be recorded.");
      }
    });
  }

  async function openAgentRun(runId) {
    var panel = UI.openModal({
      title: "Agent output",
      wide: true,
      body: '<div class="skeleton skeleton--text"></div>' +
            '<div class="skeleton skeleton--text"></div>',
      footer: '<button class="btn" data-modal-close>Close</button>',
    });
    try {
      var run = await Api.agentRun(caseId, runId);
      var out = run.output || {};
      var sections = "";

      if (run.summary) sections += "<p>" + UI.esc(run.summary) + "</p>";

      if (out.listing && Object.keys(out.listing).length) {
        sections += "<h4 style=\"margin:1rem 0 .5rem;font-size:.85rem\">Captured listing data</h4>" +
          '<dl class="dl">' + Object.keys(out.listing).map(function (k) {
            var v = out.listing[k];
            return "<div><dt>" + UI.esc(UI.titleize(k)) + "</dt><dd>" +
              UI.esc(typeof v === "boolean" ? (v ? "Yes" : "No") : v) + "</dd></div>";
          }).join("") + "</dl>";
      }

      if (run.findings && run.findings.length) {
        sections += "<h4 style=\"margin:1.25rem 0 .5rem;font-size:.85rem\">Findings</h4>" +
          findingList(run.findings, run.findings[0].kind);
      }

      if (out.notice_draft) {
        sections += "<h4 style=\"margin:1.25rem 0 .5rem;font-size:.85rem\">Notice draft</h4>" +
          '<pre class="notice-draft">' + UI.esc(out.notice_draft) + "</pre>";
      }
      if (out.gate_message) {
        sections += '<div class="banner banner--warning" style="margin-top:1rem">' +
          "<span>⚠</span><span>" + UI.esc(out.gate_message) + "</span></div>";
      }

      panel.querySelector(".modal__head h2").textContent =
        run.label + " · " + UI.titleize(run.status);
      panel.querySelector(".modal__body").innerHTML =
        '<div class="dl" style="margin-bottom:1rem">' +
          "<div><dt>Status</dt><dd>" + UI.badge(run.status) + "</dd></div>" +
          "<div><dt>Provider</dt><dd>" + UI.esc(run.provider || "-") + " / " +
            UI.esc(run.model || "-") + "</dd></div>" +
          "<div><dt>Duration</dt><dd>" + UI.esc(UI.fmtDuration(run.duration_ms)) + "</dd></div>" +
          "<div><dt>Started</dt><dd>" + UI.esc(UI.fmtDateTime(run.started_at)) + "</dd></div>" +
          (run.confidence != null
            ? "<div><dt>Confidence</dt><dd>" + Math.round(run.confidence) + "%</dd></div>" : "") +
          "<div><dt>Evidence added</dt><dd>" + (run.evidence_count || 0) + "</dd></div>" +
          "<div><dt>Correlation ID</dt><dd class=\"mono\">" +
            UI.esc(run.correlation_id || "-") + "</dd></div>" +
        "</div>" + sections +
        '<p class="card__hint" style="margin-top:1rem">' + UI.esc(run.disclaimer || "") + "</p>";
    } catch (err) {
      panel.querySelector(".modal__body").innerHTML =
        UI.emptyState("Could not load the agent output", err.message || "");
    }
  }

  /* ================================================================== */
  /* Live updates                                                        */
  /* ================================================================== */
  function connectSocket() {
    if (!window.WebSocket) return;
    try {
      var proto = location.protocol === "https:" ? "wss:" : "ws:";
      socket = new WebSocket(proto + "//" + location.host + "/ws/cases/" + caseId);
      socket.addEventListener("message", function (event) {
        var msg;
        try { msg = JSON.parse(event.data); } catch (e) { return; }
        if (msg.event === "ping" || msg.event === "connected") return;
        if (msg.event === "agent.started" || msg.event === "agent.finished") {
          load(true);
        }
        if (msg.event === "pipeline.finished") {
          UI.toast.success("AI analysis complete.");
          stopPolling();
          load(true);
        }
        if (msg.event === "pipeline.failed") {
          UI.toast(msg.message || "Analysis failed.", "error");
          stopPolling();
          load(true);
        }
      });
      socket.addEventListener("close", function () { socket = null; });
    } catch (e) { socket = null; }
  }

  /* Polling has a hard, sticky ceiling.
     The previous version stopped after 25 attempts, but `load()` restarted it
     on every response that still said ANALYZING - so a case left in ANALYZING
     (a server restarted mid-pipeline, say) polled every two seconds forever. */
  var POLL_LIMIT = 45;                    // ~90 seconds
  var pollAttempts = 0;
  var pollExhausted = false;

  function startPolling() {
    if (pollTimer || pollExhausted) return;
    pollTimer = setInterval(async function () {
      pollAttempts++;
      if (pollAttempts > POLL_LIMIT) {
        pollExhausted = true;
        stopPolling();
        renderStalled();
        return;
      }
      await load(true);
      if (current && current.status !== "ANALYZING") stopPolling();
    }, 2000);
  }

  function stopPolling() {
    if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
  }

  function renderStalled() {
    var slot = document.getElementById("gateSlot");
    if (!slot || slot.querySelector("[data-stalled]")) return;
    slot.insertAdjacentHTML("afterbegin",
      '<div class="banner banner--warning" data-stalled style="margin-bottom:1rem">' +
        "<span aria-hidden=\"true\">⚠</span><span>" +
        "<strong>This case is still marked as analysing.</strong> " +
        "Live updates have stopped after 90 seconds. The pipeline may have been " +
        "interrupted - for example by a server restart. Re-run the analysis, or " +
        "reload to check again." +
        '<div style="margin-top:.625rem;display:flex;gap:.5rem;flex-wrap:wrap">' +
          '<button class="btn btn--sm" data-stalled-refresh>Check again</button>' +
        "</div></span></div>");
    var btn = slot.querySelector("[data-stalled-refresh]");
    if (btn) {
      btn.addEventListener("click", async function () {
        pollAttempts = 0; pollExhausted = false;
        UI.setLoading(btn, true, "Checking");
        await load();
      });
    }
  }

  /* ================================================================== */
  /** Replace the loading skeletons with an explicit failure and a retry.
   *  A skeleton that never resolves is indistinguishable from a hang. */
  function renderLoadFailure(err) {
    document.getElementById("caseNumber").textContent = "Case #" + caseId;
    document.getElementById("caseTitle").textContent = "";
    document.getElementById("caseActions").innerHTML = "";

    ["confidenceHero", "statusCard", "slaCard"].forEach(function (id) {
      var el = document.getElementById(id);
      if (el) el.innerHTML = '<div class="card__hint">Unavailable</div>';
    });
    ["overview", "approvalHistory", "autonomyPanel", "evidencePanel",
     "activityPanel", "pipeline", "enforcementTimeline"].forEach(function (id) {
      var el = document.getElementById(id);
      if (el) el.innerHTML = '<div class="card__hint">Unavailable</div>';
    });

    var detail = err.code === "REQUEST_TIMEOUT"
      ? "The request to /api/cases/" + caseId + " did not return in time. " +
        "If you are running through a tunnel or proxy, check that it is " +
        "forwarding the response."
      : (err.message || "The request failed.");

    document.getElementById("gateSlot").innerHTML =
      '<div class="banner banner--danger">' +
        "<span aria-hidden=\"true\">✕</span>" +
        "<span><strong>This case could not be loaded.</strong> " +
        UI.esc(detail) +
        (err.code ? ' <span class="card__hint">(' + UI.esc(err.code) + ")</span>" : "") +
        '<div style="margin-top:.625rem;display:flex;gap:.5rem;flex-wrap:wrap">' +
          '<button class="btn btn--sm" id="retryLoad">Try again</button>' +
          '<a class="btn btn--sm" href="/cases">Back to cases</a>' +
        "</div></span></div>";

    var retry = document.getElementById("retryLoad");
    if (retry) {
      retry.addEventListener("click", async function () {
        UI.setLoading(retry, true, "Retrying");
        document.getElementById("gateSlot").innerHTML = "";
        await load();
      });
    }
  }

  async function load(silent) {
    try {
      // A background poll is not a human opening the case: it must not write
      // a CASE_VIEWED audit record every two seconds.
      var c = await Api.caseDetail(caseId, { poll: !!silent });
      current = c;
      renderHeader(c);
      renderOverview(c);
      renderPipeline(c);
      renderDecision(c);
      renderGate(c);
      renderTimeline(c);
      renderEvidence(c);
      renderApprovals(c);
      renderAutonomy(c);
      if (!silent) renderActivity();
      if (c.status === "ANALYZING") startPolling(); else stopPolling();
    } catch (err) {
      stopPolling();
      if (err.code === "NAVIGATION_ABORTED") return;

      if (err.status === 403 || err.status === 404) {
        document.getElementById("caseRoot").innerHTML =
          '<div class="card"><div class="card__body">' +
          UI.emptyState("Case not available",
            "This case does not exist, or it belongs to another vendor.",
            '<a class="btn btn--primary" href="/cases">Back to cases</a>') +
          "</div></div>";
        return;
      }

      // Anything else: say so and offer a retry. Leaving skeletons on screen
      // reads as "still loading" forever, which is what happened in the field.
      UI.handleError(err, "Could not load the case.");
      renderLoadFailure(err);
    }
  }

  (async function init() {
    // The identity lookup must never gate the page. It only decides whether
    // the approve/reject buttons are offered; if it fails, the case still
    // renders and the gate simply stays read-only.
    try {
      me = await Api.me();
    } catch (e) {
      me = null;
      if (e.code !== "NAVIGATION_ABORTED" && e.status !== 401) {
        UI.toast("Could not confirm your permissions; the approval controls " +
                 "are hidden until the page is reloaded.", "warning", 8000);
      }
    }
    await load();
    connectSocket();
    if (new URLSearchParams(location.search).get("analysing")) startPolling();
    setInterval(function () { if (!pollTimer) renderActivity(); }, 60000);
  })();
})();
