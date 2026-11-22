const caseForm = document.getElementById("caseForm");
const fileInput = document.getElementById("fileInput");
const fileList = document.getElementById("fileList");
const submitBtn = document.getElementById("submitBtn");
const demoBtn = document.getElementById("demoBtn");
const downloadMdBtn = document.getElementById("downloadMdBtn");
const statusMsg = document.getElementById("statusMsg");
const resultsContainer = document.getElementById("resultsContainer");

let lastReportData = null;

fileInput.addEventListener("change", () => {
  const names = Array.from(fileInput.files).map((f) => `${f.name} (${(f.size / 1024).toFixed(1)} KB)`);
  fileList.textContent = names.length ? `Selected: ${names.join(", ")}` : "";
});

demoBtn.addEventListener("click", async () => {
  statusMsg.textContent = "Loading pediatric AML tumor-board benchmarked demo...";
  submitBtn.disabled = true;
  try {
    const resp = await fetch("/api/demo", { method: "POST" });
    const data = await resp.json();
    if (!resp.ok) throw new Error(data.detail || "Demo failed");
    renderResults(data);
    statusMsg.textContent = "Demo loaded (offline preview).";
  } catch (err) {
    statusMsg.textContent = `Error: ${err.message}`;
  } finally {
    submitBtn.disabled = false;
  }
});

caseForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  statusMsg.textContent = "Parsing documents via BigQuery AI.PARSE_DOCUMENT / AI.CHUNK_DOCUMENT & running hybrid search...";
  submitBtn.disabled = true;

  const formData = new FormData();
  formData.append("clinical_notes", document.getElementById("clinicalNotes").value);
  formData.append("min_pub_year", document.getElementById("minPubYear").value);
  formData.append("recruiting_only", document.getElementById("recruitingOnly").checked);
  formData.append("include_private_pdfs", document.getElementById("includePrivatePdfs").checked);

  for (const file of fileInput.files) {
    formData.append("files", file);
  }

  try {
    const resp = await fetch("/api/analyze", { method: "POST", body: formData });
    const data = await resp.json();
    if (!resp.ok) throw new Error(data.detail || "Analysis failed");
    renderResults(data);
    statusMsg.textContent = `Completed (${data.case_id}).`;
  } catch (err) {
    statusMsg.textContent = `Error: ${err.message}`;
  } finally {
    submitBtn.disabled = false;
  }
});

downloadMdBtn.addEventListener("click", () => {
  if (!lastReportData) return;
  const blob = new Blob([lastReportData.markdown_report || ""], { type: "text/markdown" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `${lastReportData.case_id || "clinical-report"}.md`;
  a.click();
  URL.revokeObjectURL(url);
});

function renderResults(data) {
  lastReportData = data;
  resultsContainer.classList.remove("hidden");
  downloadMdBtn.classList.remove("hidden");

  // 1. Telemetry Banner
  const tel = data.efficiency_telemetry || { total_mb_processed: 0, steps: [] };
  const stepsHtml = (tel.steps || [])
    .map((s) => `<span class="chip">${s.step}: ${s.mb_processed} MB</span>`)
    .join("");
  document.getElementById("telemetryBanner").innerHTML = `
    <div><strong>BigQuery Zero-Full-Scan Efficiency:</strong> Total Scanned <strong>${tel.total_mb_processed} MB</strong> (vs. 500+ GB full PubMed scan)</div>
    <div class="chips">${stepsHtml}</div>
  `;

  // 2. Patient Profile
  const p = data.patient_profile || {};
  const markers = (p.actionable_biomarkers || []).map((m) => `<span class="chip">${m}</span>`).join("");
  const findings = (p.key_lab_and_diagnostic_findings || [])
    .map((f) => `<li><strong>${f.finding}</strong> <span class="meta-row">[${f.source_citation}]</span></li>`)
    .join("");
  document.getElementById("profileContent").innerHTML = `
    <p><strong>Diagnosis:</strong> ${p.primary_diagnosis || "N/A"} | <strong>Age:</strong> ${p.age_years ?? "N/A"} | <strong>Sex:</strong> ${p.sex || "N/A"}</p>
    <p>${p.patient_summary || ""}</p>
    <div><strong>Actionable Biomarkers:</strong><div class="chips">${markers}</div></div>
    <div><strong>Key Diagnostic Findings &amp; Page Citations:</strong><ul>${findings}</ul></div>
  `;

  // 3. Synthesis
  document.getElementById("synthesisContent").textContent = data.markdown_report || "";

  // 4. Benchmarked PubMed
  const pubmedHtml = (data.benchmarked_pubmed || [])
    .map((a, idx) => {
      const b = a.benchmark || {};
      const br = b.breakdown || {};
      const matched = (b.matched_biomarkers || []).map((m) => `<span class="chip chip-green">${m}</span>`).join("");
      return `
        <div class="evidence-item">
          <div class="evidence-header">
            <a class="evidence-title" href="${a.pmc_link || '#'}" target="_blank" rel="noopener">
              #${idx + 1} ${a.title || a.pmc_id}
            </a>
            <span class="score-badge">Score: ${b.total_score ?? 0} / 100</span>
          </div>
          <div class="meta-row">
            PMID: <strong>${a.pmid || "N/A"}</strong> | ${a.pmc_id || ""} | ${a.journal_name || "Unknown Journal"} (${a.pub_year || ""}) |
            ${br.evidence_hierarchy_rigor?.level || a.study_type_inferred || ""} | Retrieval: ${b.retrieval_mode || ""}
          </div>
          <div class="chips">${matched}</div>
          <div class="breakdown-grid">
            <div><strong>Biomarker/Semantic:</strong> ${br.biomarker_and_semantic_match?.score ?? 0}/40</div>
            <div><strong>Evidence Level:</strong> ${br.evidence_hierarchy_rigor?.score ?? 0}/30</div>
            <div><strong>Journal Impact (${br.journal_impact?.quartile || "N/A"}):</strong> ${br.journal_impact?.score ?? 0}/15</div>
            <div><strong>Recency (${br.recency?.pub_year || ""}):</strong> ${br.recency?.score ?? 0}/15</div>
          </div>
        </div>
      `;
    })
    .join("");
  document.getElementById("pubmedContent").innerHTML = pubmedHtml || "<p>No PubMed matches returned.</p>";

  // 5. Benchmarked Clinical Trials
  const trialsHtml = (data.benchmarked_trials || [])
    .map((t, idx) => {
      const b = t.benchmark || {};
      const br = b.breakdown || {};
      const checks = (b.eligibility_checklist || [])
        .map((c) => {
          const cls = c.status === "PASS" ? "chip-green" : c.status === "FAIL" ? "chip-red" : "chip-amber";
          return `<span class="chip ${cls}">${c.status}: ${c.criterion}</span>`;
        })
        .join("");
      return `
        <div class="evidence-item">
          <div class="evidence-header">
            <a class="evidence-title" href="${t.study_url || '#'}" target="_blank" rel="noopener">
              #${idx + 1} [${t.nct_id}] ${t.brief_title || ""}
            </a>
            <span class="score-badge">Score: ${b.total_score ?? 0} / 100</span>
          </div>
          <div class="meta-row">
            Status: <strong>${t.overall_status}</strong> | Phase: <strong>${t.phase}</strong> | Sponsor: ${t.lead_sponsor || "N/A"} | ${t.locations_summary || ""}
          </div>
          <div class="meta-row"><strong>Interventions:</strong> ${t.interventions_text || "N/A"}</div>
          <div class="chips">${checks}</div>
          <div class="breakdown-grid">
            <div><strong>Actionability:</strong> ${br.recruitment_actionability?.score ?? 0}/30</div>
            <div><strong>Biomarker/Semantic:</strong> ${br.biomarker_and_semantic_match?.score ?? 0}/35</div>
            <div><strong>Phase Maturity:</strong> ${br.phase_maturity?.score ?? 0}/20</div>
            <div><strong>Eligibility Fit:</strong> ${br.eligibility_alignment?.score ?? 0}/15</div>
          </div>
        </div>
      `;
    })
    .join("");
  document.getElementById("trialsContent").innerHTML = trialsHtml || "<p>No Clinical Trials returned.</p>";
}
