import React, { useState } from "react";
import { Loader2, ArrowRight, ArrowLeft, Plus, Trash2 } from "lucide-react";

interface SetupWizardProps {
  onComplete: () => void;
  port: number;
}

/* ── 数据结构 ──────────────────────────────────────────── */

interface Provider {
  name: string;
  client_type: "openai" | "anthropic";
  api_key: string;
  base_url: string;
}

interface ModelEntry {
  model_id: string;
  api_provider: string;
}

const BASE_URL_DEFAULTS: Record<string, string> = {
  openai: "https://api.openai.com/v1",
  anthropic: "https://api.anthropic.com",
};

const PRESET_PROVIDERS: Record<string, Omit<Provider, "api_key">> = {
  openai: { name: "OpenAI", client_type: "openai", base_url: "https://api.openai.com/v1" },
  anthropic: { name: "Anthropic", client_type: "anthropic", base_url: "https://api.anthropic.com" },
};

/* ── 主组件 ────────────────────────────────────────────── */

const SetupWizard: React.FC<SetupWizardProps> = ({ onComplete, port }) => {
  const TOTAL_STEPS = 5;
  const [step, setStep] = useState(1);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [error, setError] = useState("");

  // Step 1 — API Providers
  const [providers, setProviders] = useState<Provider[]>([
    { name: "OpenAI", client_type: "openai", api_key: "", base_url: "https://api.openai.com/v1" },
  ]);
  const [editingProvider, setEditingProvider] = useState(0);

  // Step 1 — Import
  const [importPath, setImportPath] = useState("");
  const [isImporting, setIsImporting] = useState(false);
  const [showImport, setShowImport] = useState(false);
  const [importSuccess, setImportSuccess] = useState(false);

  // Step 2 — Models
  const [models, setModels] = useState<ModelEntry[]>([
    { model_id: "gpt-4o", api_provider: "OpenAI" },
  ]);

  // Step 3 — Role defaults (store model name = "ProviderName/ModelId")
  const [chatModelName, setChatModelName] = useState("");
  const [coderModelName, setCoderModelName] = useState("");
  const [researcherModelName, setResearcherModelName] = useState("");
  const [reviewerModelName, setReviewerModelName] = useState("");
  const [titleModelName, setTitleModelName] = useState("");
  const [showAdvancedRoles, setShowAdvancedRoles] = useState(false);

  // Step 4 — Personality
  const [botName, setBotName] = useState("MoFox");
  const [botBio, setBotBio] = useState("我是一个AI编程助手，致力于帮助你编写高质量代码。");
  const [personalityCore, setPersonalityCore] = useState("友好、专业、简洁");
  const [replyStyle, setReplyStyle] = useState("自然口语化");
  const [botIdentity, setBotIdentity] = useState("AI编程助手");

  // Step 5 — MCP
  const [mcpServers, setMcpServers] = useState<Array<{ name: string; command: string; args: string }>>([]);

  /* ── 辅助 ────────────────────────────────────────────── */

  const modelName = (m: ModelEntry) => `${m.api_provider}/${m.model_id}`;
  const allModelNames = models.map(modelName);
  const firstModelName = allModelNames[0] ?? "";

  // 当 chatModelName 未设置时，自动跟随第一个模型
  const effectiveChat = chatModelName || firstModelName;

  /* ── Provider CRUD ───────────────────────────────────── */

  const addProvider = () => {
    setProviders([...providers, { name: "", client_type: "openai", api_key: "", base_url: "" }]);
    setEditingProvider(providers.length);
  };

  const removeProvider = (i: number) => {
    if (providers.length <= 1) return;
    const next = providers.filter((_, idx) => idx !== i);
    setProviders(next);
    setEditingProvider(Math.min(editingProvider, next.length - 1));
    // 同步移除关联模型
    setModels((prev) => prev.filter((m) => m.api_provider !== providers[i].name));
  };

  const updateProvider = (i: number, patch: Partial<Provider>) => {
    const next = [...providers];
    next[i] = { ...next[i], ...patch };
    // 若修改了 name，同步更新模型中的 api_provider 引用
    if (patch.name !== undefined) {
      const oldName = providers[i].name;
      setModels((prev) => prev.map((m) => (m.api_provider === oldName ? { ...m, api_provider: patch.name! } : m)));
    }
    setProviders(next);
  };

  const applyPreset = (i: number, preset: string) => {
    const p = PRESET_PROVIDERS[preset];
    if (!p) return;
    updateProvider(i, { ...p });
  };

  /* ── Model CRUD ──────────────────────────────────────── */

  const addModel = () => {
    const defaultProv = providers[0]?.name ?? "";
    setModels([...models, { model_id: "", api_provider: defaultProv }]);
  };

  const removeModel = (i: number) => {
    setModels(models.filter((_, idx) => idx !== i));
  };

  const updateModel = (i: number, patch: Partial<ModelEntry>) => {
    const next = [...models];
    next[i] = { ...next[i], ...patch };
    setModels(next);
  };

  /* ── MCP helpers ─────────────────────────────────────── */

  const addMcp = () => setMcpServers([...mcpServers, { name: "", command: "", args: "" }]);
  const removeMcp = (i: number) => setMcpServers(mcpServers.filter((_, idx) => idx !== i));
  const updateMcp = (i: number, field: string, value: string) => {
    const next = [...mcpServers];
    next[i] = { ...next[i], [field]: value };
    setMcpServers(next);
  };

  /* ── Step validation ─────────────────────────────────── */

  const nextStep = () => {
    setError("");
    if (step === 1) {
      if (providers.some((p) => !p.name.trim())) { setError("请填写所有提供商名称"); return; }
      if (providers.some((p) => !p.api_key.trim())) { setError("请填写所有 API Key"); return; }
      // 自动为新提供商填充 base_url
      providers.forEach((p, i) => {
        if (!p.base_url.trim()) {
          updateProvider(i, { base_url: BASE_URL_DEFAULTS[p.client_type] ?? "" });
        }
      });
    }
    if (step === 2) {
      if (models.length === 0) { setError("至少配置一个模型"); return; }
      if (models.some((m) => !m.model_id.trim())) { setError("请填写所有模型 ID"); return; }
    }
    if (step === 3) {
      if (!effectiveChat) { setError("请为对话角色选择模型"); return; }
    }
    setStep(step + 1);
  };

  const prevStep = () => { setError(""); setStep(step - 1); };

  /* ── Import ──────────────────────────────────────────── */

  const handleImport = async () => {
    if (!importPath.trim()) return;
    setIsImporting(true);
    setError("");
    setImportSuccess(false);
    try {
      const res = await fetch(`http://127.0.0.1:${port}/api/setup/import`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ config_dir: importPath.trim() }),
      });
      const json = await res.json();
      if (json.status !== "ok") throw new Error(json.message || "导入失败");
      const d = json.data;
      console.log("[Import] 响应数据:", d);

      // 回填 Providers
      const impProviders: Provider[] = (d.api_providers ?? []).map((p: any) => ({
        name: p.name ?? "",
        client_type: (p.client_type ?? "openai") as "openai" | "anthropic",
        api_key: p.api_key ?? "",
        base_url: p.base_url ?? "",
      }));
      if (impProviders.length > 0) { setProviders(impProviders); setEditingProvider(0); }

      // 回填 Models
      const impModels: ModelEntry[] = (d.models ?? []).map((m: any) => ({
        model_id: m.model_id ?? "",
        api_provider: m.api_provider ?? impProviders[0]?.name ?? "",
      }));
      if (impModels.length > 0) setModels(impModels);

      // 回填 Role defaults
      const roles = d.roles ?? {};
      setChatModelName(roles.main ?? "");
      setCoderModelName(roles.coder ?? "");
      setResearcherModelName(roles.researcher ?? "");
      setReviewerModelName(roles.reviewer ?? "");
      setTitleModelName(roles.title ?? "");

      // 回填 Personality
      const personality = d.personality ?? {};
      if (personality.nickname) setBotName(personality.nickname);
      if (personality.background_story) setBotBio(personality.background_story);
      if (personality.personality_core) setPersonalityCore(personality.personality_core);
      if (personality.reply_style) setReplyStyle(personality.reply_style);
      if (personality.identity) setBotIdentity(personality.identity);

      // 回填 MCP
      const mcpList = (d.mcp_servers ?? []).map((s: any) => ({
        name: s.name ?? "",
        command: s.command ?? "",
        args: Array.isArray(s.args) ? s.args.join(" ") : (s.args ?? ""),
      }));
      if (mcpList.length > 0) setMcpServers(mcpList);

      console.log(`[Import] 成功: ${impProviders.length} 个提供商, ${impModels.length} 个模型`);
      setImportSuccess(true);
    } catch (err: any) {
      console.error("[Import] 失败:", err);
      setError(err.message || "导入失败");
    } finally {
      setIsImporting(false);
    }
  };

  /* ── Submit ──────────────────────────────────────────── */

  const handleSubmit = async () => {
    setIsSubmitting(true);
    setError("");

    const chat = chatModelName || firstModelName;
    const coder = coderModelName || chat;
    const researcher = researcherModelName || chat;
    const reviewer = reviewerModelName || chat;
    const title = titleModelName || chat;

    const parsedMcp = mcpServers
      .filter((s) => s.name.trim() && s.command.trim())
      .map((s) => ({
        name: s.name.trim(),
        command: s.command.trim(),
        args: s.args.split(" ").filter((a) => a.trim()),
        enabled: true,
      }));

    const configPayload = {
      api_providers: providers.map((p) => ({
        name: p.name.trim(),
        client_type: p.client_type,
        api_key: p.api_key.trim(),
        base_url: p.base_url.trim() || (BASE_URL_DEFAULTS[p.client_type] ?? ""),
      })),
      models: models.map((m) => ({
        model_id: m.model_id.trim(),
        api_provider: m.api_provider,
      })),
      roles: { main: chat, coder, researcher, reviewer, title },
      personality: {
        nickname: botName.trim(),
        background_story: botBio.trim(),
        personality_core: personalityCore.trim(),
        reply_style: replyStyle.trim(),
        identity: botIdentity.trim(),
      },
      model_profiles: [],
      mcp_servers: parsedMcp,
    };

    try {
      const res = await fetch(`http://127.0.0.1:${port}/api/setup`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(configPayload),
      });
      if (!res.ok) {
        const errData = await res.json();
        throw new Error(errData.message || "提交配置失败");
      }
      onComplete();
    } catch (err: any) {
      setError(err.message || "发生未知错误");
      setIsSubmitting(false);
    }
  };

  /* ── UI helpers ──────────────────────────────────────── */

  const cardBorder = "1px solid #e5e7eb";
  const itemCard: React.CSSProperties = {
    border: cardBorder, borderRadius: "8px", padding: "10px 12px", marginBottom: "8px", position: "relative",
  };

  /* ── Render ──────────────────────────────────────────── */

  return (
    <div className="setup-container">
      <div className="setup-card" style={{ maxWidth: "580px", minHeight: "520px", display: "flex", flexDirection: "column" }}>

        {/* Progress bar */}
        <div style={{ display: "flex", width: "100%", justifyContent: "space-between", marginBottom: "20px" }}>
          {Array.from({ length: TOTAL_STEPS }, (_, i) => i + 1).map((s) => (
            <div key={s} style={{
              flex: 1, height: "4px",
              backgroundColor: step >= s ? "#3b82f6" : "rgba(156,163,175,0.3)",
              margin: "0 3px", borderRadius: "2px", transition: "background-color 0.3s",
            }} />
          ))}
        </div>

        {/* Header */}
        <div className="setup-header">
          <h2 className="setup-title">
            {step === 1 && "配置 API 服务商"}
            {step === 2 && "配置模型列表"}
            {step === 3 && "分配角色默认模型"}
            {step === 4 && "配置人设信息"}
            {step === 5 && "配置 MCP 服务 (可选)"}
          </h2>
          <p className="setup-subtitle">
            {step === 1 && "添加一个或多个 AI 服务商（支持 OpenAI 兼容与 Anthropic）"}
            {step === 2 && "为每个模型指定模型 ID 并关联到对应的服务商"}
            {step === 3 && "从上一步配置的模型中，为各角色选择默认使用的模型"}
            {step === 4 && "定制助手的名称和核心系统提示"}
            {step === 5 && "添加本地或远程的 MCP (Model Context Protocol) 工具端点"}
          </p>
        </div>

        {/* Content */}
        <div style={{ flex: 1, width: "100%", overflowY: "auto" }}>

          {/* ══ STEP 1: API Providers ══ */}
          {step === 1 && (
            <>
              {/* Import panel */}
              <div style={{ border: importSuccess ? "1px solid #10b981" : "1px dashed #d1d5db", borderRadius: "8px", padding: "8px 12px", marginBottom: "12px", transition: "border-color 0.3s" }}>
                <button type="button" onClick={() => setShowImport(!showImport)}
                  style={{ background: "none", border: "none", color: importSuccess ? "#10b981" : "#3b82f6", cursor: "pointer", fontSize: "13px", padding: 0, display: "flex", alignItems: "center", gap: "4px", width: "100%" }}>
                  {showImport ? "▾" : "▸"} {importSuccess ? "✓ 配置已导入" : "从已有 Neo-MoFox 实例导入配置"}
                </button>
                {showImport && (
                  <>
                    <div style={{ marginTop: "8px", display: "flex", gap: "8px", alignItems: "flex-end" }}>
                      <div style={{ flex: 1 }}>
                        <input className="setup-input" style={{ padding: "7px" }} value={importPath}
                          onChange={(e) => { setImportPath(e.target.value); setImportSuccess(false); }}
                          placeholder="config 目录路径，如 C:\Projects\Neo-MoFox\config" />
                      </div>
                      <button type="button" onClick={handleImport} disabled={isImporting || !importPath.trim()}
                        style={{ padding: "7px 14px", borderRadius: "7px", border: "1px solid #3b82f6", background: "#3b82f6", color: "#fff", cursor: "pointer", fontSize: "13px", fontWeight: 600, whiteSpace: "nowrap", opacity: (isImporting || !importPath.trim()) ? 0.5 : 1 }}>
                        {isImporting ? "导入中…" : "导入"}
                      </button>
                    </div>
                    {importSuccess && (
                      <div style={{ marginTop: "6px", fontSize: "12px", color: "#10b981", display: "flex", alignItems: "center", gap: "4px" }}>
                        ✓ 导入成功，已填充所有配置项，可点击"下一步"继续
                      </div>
                    )}
                  </>
                )}
              </div>

              {/* Provider tabs */}
              <div style={{ display: "flex", gap: "4px", flexWrap: "wrap", marginBottom: "10px" }}>
                {providers.map((p, i) => (
                  <button key={i} type="button" onClick={() => setEditingProvider(i)}
                    style={{
                      padding: "5px 12px", borderRadius: "6px", fontSize: "12px", cursor: "pointer",
                      border: editingProvider === i ? "1px solid #3b82f6" : "1px solid #d1d5db",
                      background: editingProvider === i ? "#eff6ff" : "transparent",
                      color: editingProvider === i ? "#1d4ed8" : "#374151",
                      display: "flex", alignItems: "center", gap: "4px",
                    }}>
                    {p.name || `提供商 ${i + 1}`}
                    {providers.length > 1 && (
                      <Trash2 size={11} onClick={(e) => { e.stopPropagation(); removeProvider(i); }}
                        style={{ cursor: "pointer", color: "#9ca3af" }} />
                    )}
                  </button>
                ))}
                <button type="button" onClick={addProvider}
                  style={{ padding: "5px 10px", borderRadius: "6px", fontSize: "12px", cursor: "pointer", border: "1px dashed #9ca3af", background: "transparent", color: "#6b7280" }}>
                  <Plus size={12} /> 添加
                </button>
              </div>

              {/* Active provider form */}
              {providers[editingProvider] && (() => {
                const p = providers[editingProvider];
                const i = editingProvider;
                return (
                  <>
                    {/* Preset quick-fill */}
                    <div style={{ display: "flex", gap: "6px", marginBottom: "10px" }}>
                      {Object.keys(PRESET_PROVIDERS).map((key) => (
                        <button key={key} type="button" onClick={() => applyPreset(i, key)}
                          style={{ padding: "4px 10px", borderRadius: "6px", fontSize: "12px", cursor: "pointer", border: "1px solid #d1d5db", background: p.name === PRESET_PROVIDERS[key].name ? "#eff6ff" : "transparent" }}>
                          {PRESET_PROVIDERS[key].name}
                        </button>
                      ))}
                      <button type="button" onClick={() => applyPreset(i, "custom")}
                        style={{ padding: "4px 10px", borderRadius: "6px", fontSize: "12px", cursor: "pointer", border: "1px solid #d1d5db", background: "transparent" }}>
                        自定义
                      </button>
                    </div>

                    <div style={{ display: "flex", gap: "10px" }}>
                      <div className="setup-form-group" style={{ flex: 1 }}>
                        <label className="setup-label">名称</label>
                        <input className="setup-input" value={p.name} onChange={(e) => updateProvider(i, { name: e.target.value })} placeholder="如: DeepSeek" />
                      </div>
                      <div className="setup-form-group" style={{ flex: 1 }}>
                        <label className="setup-label">Client Type</label>
                        <select title="Client Type" className="setup-input" value={p.client_type} onChange={(e) => updateProvider(i, { client_type: e.target.value as "openai" | "anthropic" })}>
                          <option value="openai">OpenAI 兼容</option>
                          <option value="anthropic">Anthropic</option>
                        </select>
                      </div>
                    </div>
                    <div className="setup-form-group">
                      <label className="setup-label">API Key</label>
                      <input type="password" className="setup-input" value={p.api_key} onChange={(e) => updateProvider(i, { api_key: e.target.value })} placeholder="API Key" autoComplete="off" />
                    </div>
                    <div className="setup-form-group">
                      <label className="setup-label">Base URL</label>
                      <input className="setup-input" value={p.base_url} onChange={(e) => updateProvider(i, { base_url: e.target.value })} placeholder="留空使用默认，或填中转地址" />
                    </div>
                  </>
                );
              })()}
            </>
          )}

          {/* ══ STEP 2: Models ══ */}
          {step === 2 && (
            <div style={{ display: "flex", flexDirection: "column", gap: "8px", maxHeight: "320px", overflowY: "auto", paddingRight: "4px" }}>
              {models.map((m, i) => (
                <div key={i} style={itemCard}>
                  <button title="删除模型" onClick={() => removeModel(i)} style={{ position: "absolute", top: "8px", right: "8px", background: "none", border: "none", color: "#ef4444", cursor: "pointer" }}>
                    <Trash2 size={14} />
                  </button>
                  <div style={{ display: "flex", gap: "8px" }}>
                    <div style={{ flex: 2 }}>
                      <label className="setup-label" style={{ fontSize: "11px" }}>模型 ID</label>
                      <input className="setup-input" style={{ padding: "6px" }} value={m.model_id}
                        onChange={(e) => updateModel(i, { model_id: e.target.value })} placeholder="如: gpt-4o, claude-3-7-sonnet-latest" />
                    </div>
                    <div style={{ flex: 1 }}>
                      <label className="setup-label" style={{ fontSize: "11px" }}>所属服务商</label>
                      <select title="所属服务商" className="setup-input" style={{ padding: "6px" }} value={m.api_provider}
                        onChange={(e) => updateModel(i, { api_provider: e.target.value })}>
                        {providers.map((p) => <option key={p.name} value={p.name}>{p.name}</option>)}
                      </select>
                    </div>
                  </div>
                  <div style={{ fontSize: "11px", color: "#6b7280", marginTop: "4px" }}>
                    → 内部名称: <code style={{ background: "#f3f4f6", padding: "1px 4px", borderRadius: "3px" }}>{modelName(m)}</code>
                  </div>
                </div>
              ))}
              <button type="button" onClick={addModel}
                style={{ display: "flex", alignItems: "center", justifyContent: "center", gap: "6px", padding: "9px", background: "transparent", border: "1px dashed #9ca3af", borderRadius: "8px", color: "#4b5563", cursor: "pointer" }}>
                <Plus size={14} /> 添加模型
              </button>
            </div>
          )}

          {/* ══ STEP 3: Role defaults ══ */}
          {step === 3 && (
            <>
              <div className="setup-form-group">
                <label className="setup-label">对话 / 主模型 (Main)</label>
                <select title="对话/主模型" className="setup-input" value={chatModelName || firstModelName} onChange={(e) => setChatModelName(e.target.value)}>
                  {allModelNames.map((n) => <option key={n} value={n}>{n}</option>)}
                </select>
              </div>
              <div className="setup-form-group">
                <label className="setup-label">代码模型 (Coder)</label>
                <select title="代码模型" className="setup-input" value={coderModelName || effectiveChat} onChange={(e) => setCoderModelName(e.target.value)}>
                  {allModelNames.map((n) => <option key={n} value={n}>{n}</option>)}
                </select>
              </div>

              <button type="button" onClick={() => setShowAdvancedRoles(!showAdvancedRoles)}
                style={{ background: "none", border: "none", color: "#3b82f6", cursor: "pointer", fontSize: "13px", padding: "4px 0", marginTop: "2px", display: "flex", alignItems: "center", gap: "4px" }}>
                {showAdvancedRoles ? "▾" : "▸"} 高级：为更多角色指定不同模型
              </button>
              {showAdvancedRoles && (
                <>
                  {([
                    ["研究员 (Researcher)", researcherModelName, setResearcherModelName],
                    ["审查员 (Reviewer)", reviewerModelName, setReviewerModelName],
                    ["标题生成 (Title)", titleModelName, setTitleModelName],
                  ] as const).map(([label, val, setter]) => (
                    <div className="setup-form-group" key={label}>
                      <label className="setup-label" style={{ fontSize: "12px", color: "#6b7280" }}>{label}</label>
                      <select title={label} className="setup-input" value={val || effectiveChat} onChange={(e) => setter(e.target.value)}>
                        {allModelNames.map((n) => <option key={n} value={n}>{n}</option>)}
                      </select>
                    </div>
                  ))}
                </>
              )}
            </>
          )}

          {/* ══ STEP 4: Personality ══ */}
          {step === 4 && (
            <div style={{ display: "flex", flexDirection: "column", gap: "8px", maxHeight: "360px", overflowY: "auto", paddingRight: "4px" }}>
              <div style={{ display: "flex", gap: "10px" }}>
                <div className="setup-form-group" style={{ flex: 1 }}>
                  <label className="setup-label">助手名称</label>
                  <input className="setup-input" value={botName} onChange={(e) => setBotName(e.target.value)} placeholder="如: MoFox" />
                </div>
                <div className="setup-form-group" style={{ flex: 1 }}>
                  <label className="setup-label">身份特征</label>
                  <input className="setup-input" value={botIdentity} onChange={(e) => setBotIdentity(e.target.value)} placeholder="如: AI编程助手" />
                </div>
              </div>
              <div className="setup-form-group">
                <label className="setup-label">核心性格</label>
                <input className="setup-input" value={personalityCore} onChange={(e) => setPersonalityCore(e.target.value)} placeholder="如: 友好、专业、简洁" />
              </div>
              <div className="setup-form-group">
                <label className="setup-label">表达风格</label>
                <input className="setup-input" value={replyStyle} onChange={(e) => setReplyStyle(e.target.value)} placeholder="如: 自然口语化、正式、幽默" />
              </div>
              <div className="setup-form-group">
                <label className="setup-label">背景故事 / 核心设定</label>
                <textarea className="setup-input" value={botBio} onChange={(e) => setBotBio(e.target.value)} rows={4} style={{ resize: "none" }} placeholder="描述助手的世界观和背景设定（不会主动复述，仅作背景知识）" />
              </div>
            </div>
          )}

          {/* ══ STEP 5: MCP ══ */}
          {step === 5 && (
            <div style={{ display: "flex", flexDirection: "column", gap: "12px", maxHeight: "280px", overflowY: "auto", paddingRight: "6px" }}>
              {mcpServers.length === 0 ? (
                <div style={{ textAlign: "center", color: "#6b7280", padding: "16px 0" }}>
                  <p style={{ fontSize: "14px", marginBottom: "12px" }}>尚未配置任何 MCP 服务，可直接跳过。</p>
                </div>
              ) : mcpServers.map((server, i) => (
                <div key={i} style={itemCard}>
                  <button title="删除 MCP" onClick={() => removeMcp(i)} style={{ position: "absolute", top: "8px", right: "8px", background: "none", border: "none", color: "#ef4444", cursor: "pointer" }}>
                    <Trash2 size={14} />
                  </button>
                  <div style={{ display: "flex", gap: "8px", marginBottom: "6px" }}>
                    <div style={{ flex: 1 }}>
                      <label className="setup-label" style={{ fontSize: "11px" }}>名称</label>
                      <input className="setup-input" style={{ padding: "6px" }} value={server.name} onChange={(e) => updateMcp(i, "name", e.target.value)} placeholder="fetch" />
                    </div>
                    <div style={{ flex: 1 }}>
                      <label className="setup-label" style={{ fontSize: "11px" }}>命令</label>
                      <input className="setup-input" style={{ padding: "6px" }} value={server.command} onChange={(e) => updateMcp(i, "command", e.target.value)} placeholder="uvx" />
                    </div>
                  </div>
                  <div>
                    <label className="setup-label" style={{ fontSize: "11px" }}>参数（空格分隔）</label>
                    <input className="setup-input" style={{ padding: "6px" }} value={server.args} onChange={(e) => updateMcp(i, "args", e.target.value)} placeholder="mcp-server-fetch" />
                  </div>
                </div>
              ))}
              <button type="button" onClick={addMcp}
                style={{ display: "flex", alignItems: "center", justifyContent: "center", gap: "6px", padding: "9px", background: "transparent", border: "1px dashed #9ca3af", borderRadius: "8px", color: "#4b5563", cursor: "pointer" }}>
                <Plus size={14} /> 添加 MCP 服务
              </button>
            </div>
          )}
        </div>

        {/* Error */}
        {error && <div className="setup-error">{error}</div>}

        {/* Nav buttons */}
        <div style={{ display: "flex", gap: "12px", width: "100%", marginTop: "20px" }}>
          {step > 1 && (
            <button type="button" className="setup-button"
              style={{ backgroundColor: "transparent", border: "1px solid #d1d5db", color: "#374151", flex: 1 }}
              onClick={prevStep} disabled={isSubmitting}>
              <ArrowLeft size={16} /> 上一步
            </button>
          )}
          {step < TOTAL_STEPS ? (
            <button type="button" className="setup-button"
              style={{ flex: step === 1 ? "none" : 2, width: step === 1 ? "100%" : "auto" }}
              onClick={nextStep}>
              下一步 <ArrowRight size={16} />
            </button>
          ) : (
            <button type="button" className="setup-button" style={{ flex: 2 }}
              onClick={handleSubmit} disabled={isSubmitting}>
              {isSubmitting
                ? <><Loader2 className="splash-spinner-icon" size={16} style={{ width: "16px", height: "16px", color: "#fff" }} /> 配置中...</>
                : "完成并启动"}
            </button>
          )}
        </div>
      </div>
    </div>
  );
};

export default SetupWizard;
