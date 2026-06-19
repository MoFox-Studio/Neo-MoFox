import React, { useState, useEffect } from 'react';
import { User, Cpu, Network, RefreshCw, Plus, Trash2, Settings as SettingsIcon, Info, ChevronDown, ChevronRight } from 'lucide-react';

interface SettingsModalProps {
  port: number;
  onClose: () => void;
}

const SettingsModal: React.FC<SettingsModalProps> = ({ port, onClose }) => {
  const [activeTab, setActiveTab] = useState('general');
  const [config, setConfig] = useState<any>(null);
  const [saveSuccess, setSaveSuccess] = useState(false);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [version, setVersion] = useState<Record<string, string> | null>(null);
  const [expandedModel, setExpandedModel] = useState<number | null>(null);

  useEffect(() => {
    fetch(`http://127.0.0.1:${port}/api/version`)
      .then(res => res.json())
      .then(data => setVersion(data))
      .catch(() => {});
  }, [port]);

  useEffect(() => {
    fetch(`http://127.0.0.1:${port}/api/settings`)
      .then(res => res.json())
      .then(data => {
        if (data.status === 'error') {
          setError(data.message);
        } else {
          setConfig(data);
        }
        setLoading(false);
      })
      .catch(err => {
        console.error("Failed to load settings:", err);
        setError("加载设置失败，请检查后端是否正常运行。");
        setLoading(false);
      });
  }, [port]);

  const handleSave = async () => {
    setSaving(true);
    setSaveSuccess(false);
    try {
      const parsedConfig = JSON.parse(JSON.stringify(config));
      if (parsedConfig.models) {
        parsedConfig.models = parsedConfig.models.map((m: any) => {
          let mc = m.max_context;
          if (typeof mc === 'string' && mc.toLowerCase().endsWith('k')) {
            const num = parseFloat(mc.slice(0, -1));
            if (!isNaN(num)) mc = num * 1024;
          } else if (typeof mc === 'string') {
            const num = parseInt(mc, 10);
            mc = isNaN(num) ? undefined : num;
          }
          return { ...m, max_context: mc };
        });
      }

      const res = await fetch(`http://127.0.0.1:${port}/api/settings`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(parsedConfig)
      });
      const data = await res.json();
      if (data.status === 'error') {
        setError(data.message);
      } else {
        setSaveSuccess(true);
        setTimeout(() => {
          onClose();
        }, 1500);
      }
    } catch (err) {
      console.error("Failed to save settings:", err);
      setError("保存设置失败！");
    } finally {
      setSaving(false);
    }
  };

  const updateNestedConfig = (path: string[], value: any) => {
    setConfig((prev: any) => {
      const newConfig = { ...prev };
      let current = newConfig;
      for (let i = 0; i < path.length - 1; i++) {
        current = current[path[i]];
      }
      current[path[path.length - 1]] = value;
      return newConfig;
    });
  };

  const updateProvider = (index: number, field: string, value: string) => {
    setConfig((prev: any) => {
      const newConfig = { ...prev };
      newConfig.api_providers[index][field] = value;
      return newConfig;
    });
  };

  const updateModel = (index: number, field: string, value: any) => {
    setConfig((prev: any) => {
      const newConfig = { ...prev };
      newConfig.models[index][field] = value;
      return newConfig;
    });
  };

  const updateMcpServer = (index: number, field: string, value: any) => {
    setConfig((prev: any) => {
      const newConfig = { ...prev };
      newConfig.mcp_servers[index][field] = value;
      return newConfig;
    });
  };

  const updateCodingAgent = (field: string, value: any) => {
    setConfig((prev: any) => {
      const newConfig = { ...prev };
      if (!newConfig.coding_agent) {
        newConfig.coding_agent = { tui_username: 'User', preferred_terminal: '', max_parallel_researchers: 6, cache_ttl_hours: 24 };
      }
      newConfig.coding_agent[field] = value;
      return newConfig;
    });
  };

  if (loading) {
    return <div className="flex items-center justify-center h-full"><RefreshCw className="animate-spin text-blue-500 w-8 h-8" /></div>;
  }

  if (error && !config) {
    return <div className="p-8 text-center text-red-500 font-medium">{error}</div>;
  }

  const tabs = [
    { id: 'general', label: '常规', icon: User },
    { id: 'models', label: '模型与 API', icon: Cpu },
    { id: 'mcp', label: 'MCP 服务器', icon: Network },
    { id: 'advanced', label: '高级设置', icon: SettingsIcon },
    { id: 'about', label: '关于', icon: Info },
  ];

  return (
    <div className="flex h-full bg-white dark:bg-gray-950">
      {/* 侧边栏 */}
      <div className="w-56 border-r border-gray-200 dark:border-gray-800 bg-gray-50/50 dark:bg-gray-900/30 flex flex-col shrink-0">
        <div className="p-4 pt-5">
          <h3 className="text-xs font-semibold text-gray-500 dark:text-gray-400 uppercase tracking-wider mb-3">设置类别</h3>
          <div className="space-y-1">
            {tabs.map(tab => (
              <button
                key={tab.id}
                onClick={() => setActiveTab(tab.id)}
                className={`w-full flex items-center gap-3 px-3 py-2 text-sm font-medium rounded-lg transition-colors ${
                  activeTab === tab.id
                    ? 'bg-blue-50 text-blue-700 dark:bg-blue-900/30 dark:text-blue-400 shadow-sm'
                    : 'text-gray-700 dark:text-gray-300 hover:bg-gray-200/50 dark:hover:bg-gray-800/80'
                }`}
              >
                <tab.icon size={16} />
                {tab.label}
              </button>
            ))}
          </div>
        </div>
      </div>

      {/* 内容区 */}
      <div className="flex-1 flex flex-col min-w-0 relative">
        <div className="flex-1 overflow-y-auto p-6 lg:p-8 relative">
          <div className="max-w-4xl mx-auto space-y-6 pb-10">
            
            {activeTab === 'general' && (
              <div className="space-y-5 animate-in fade-in slide-in-from-bottom-2 duration-300">
                <div>
                  <h2 className="text-lg font-semibold text-gray-900 dark:text-white mb-1">常规设置</h2>
                  <p className="text-sm text-gray-500 dark:text-gray-400">调整助手的基础属性和行为习惯。</p>
                </div>
                
                <div className="space-y-4">
                  <div className="bg-white dark:bg-gray-900 border border-gray-200 dark:border-gray-800 rounded-xl p-5 shadow-sm space-y-4">
                    <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                      <div className="space-y-1.5">
                        <label className="text-sm font-medium text-gray-700 dark:text-gray-300">AI 昵称 (Nickname)</label>
                        <input type="text" value={config?.personality?.nickname || ''} onChange={(e) => updateNestedConfig(['personality', 'nickname'], e.target.value)} className="w-full px-3 py-2 border border-gray-300 dark:border-gray-700 rounded-lg text-sm bg-gray-50 dark:bg-gray-950 focus:ring-2 focus:ring-blue-500/50 outline-none" placeholder="例如: MoFox" />
                      </div>
                      <div className="space-y-1.5">
                        <label className="text-sm font-medium text-gray-700 dark:text-gray-300">别名 (Alias Names)</label>
                        <input type="text" value={config?.personality?.alias_names?.join(', ') || ''} onChange={(e) => updateNestedConfig(['personality', 'alias_names'], e.target.value.split(',').map(s => s.trim()).filter(Boolean))} className="w-full px-3 py-2 border border-gray-300 dark:border-gray-700 rounded-lg text-sm bg-gray-50 dark:bg-gray-950 focus:ring-2 focus:ring-blue-500/50 outline-none" placeholder="例如: 小狐狸, 莫狐" />
                      </div>
                    </div>
                  </div>
                  
                  <div className="bg-white dark:bg-gray-900 border border-gray-200 dark:border-gray-800 rounded-xl p-5 shadow-sm space-y-4">
                    <div className="space-y-1.5">
                      <label className="text-sm font-medium text-gray-700 dark:text-gray-300">身份设定 (System Identity)</label>
                      <textarea value={config?.personality?.identity || ''} onChange={(e) => updateNestedConfig(['personality', 'identity'], e.target.value)} rows={2} className="w-full px-3 py-2 border border-gray-300 dark:border-gray-700 rounded-lg text-sm bg-gray-50 dark:bg-gray-950 focus:ring-2 focus:ring-blue-500/50 outline-none resize-none" placeholder="描述 AI 的背景和基础系统设定" />
                    </div>
                    <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                      <div className="space-y-1.5">
                        <label className="text-sm font-medium text-gray-700 dark:text-gray-300">核心设定 (Core)</label>
                        <textarea value={config?.personality?.personality_core || ''} onChange={(e) => updateNestedConfig(['personality', 'personality_core'], e.target.value)} rows={3} className="w-full px-3 py-2 border border-gray-300 dark:border-gray-700 rounded-lg text-sm bg-gray-50 dark:bg-gray-950 focus:ring-2 focus:ring-blue-500/50 outline-none resize-none" placeholder="AI 的核心性格设定" />
                      </div>
                      <div className="space-y-1.5">
                        <label className="text-sm font-medium text-gray-700 dark:text-gray-300">扩展设定 (Side)</label>
                        <textarea value={config?.personality?.personality_side || ''} onChange={(e) => updateNestedConfig(['personality', 'personality_side'], e.target.value)} rows={3} className="w-full px-3 py-2 border border-gray-300 dark:border-gray-700 rounded-lg text-sm bg-gray-50 dark:bg-gray-950 focus:ring-2 focus:ring-blue-500/50 outline-none resize-none" placeholder="AI 的扩展细节、口头禅等" />
                      </div>
                    </div>
                    <div className="space-y-1.5">
                      <label className="text-sm font-medium text-gray-700 dark:text-gray-300">回复风格 (Reply Style)</label>
                      <input type="text" value={config?.personality?.reply_style || ''} onChange={(e) => updateNestedConfig(['personality', 'reply_style'], e.target.value)} className="w-full px-3 py-2 border border-gray-300 dark:border-gray-700 rounded-lg text-sm bg-gray-50 dark:bg-gray-950 focus:ring-2 focus:ring-blue-500/50 outline-none" placeholder="例如: 自然口语化" />
                    </div>
                  </div>
                </div>
              </div>
            )}

            {activeTab === 'models' && (
              <div className="space-y-6 animate-in fade-in slide-in-from-bottom-2 duration-300">
                <div>
                  <h2 className="text-lg font-semibold text-gray-900 dark:text-white mb-1">模型与 API</h2>
                  <p className="text-sm text-gray-500 dark:text-gray-400">配置大型语言模型 API 密钥、模型列表及角色绑定。</p>
                </div>

                {/* Providers */}
                <div className="space-y-3">
                  <div className="flex items-center justify-between">
                    <h3 className="text-sm font-semibold text-gray-900 dark:text-white">API 供应商</h3>
                    <button onClick={() => setConfig((prev: any) => ({ ...prev, api_providers: [...prev.api_providers, { name: 'new_provider', client_type: 'openai', api_key: '', base_url: '' }] }))} className="flex items-center gap-1.5 text-xs text-blue-600 dark:text-blue-400 font-medium hover:underline px-2 bg-blue-50 dark:bg-blue-900/30 py-1 rounded">
                      <Plus size={14} /> 添加供应商
                    </button>
                  </div>
                  
                  <div className="grid grid-cols-1 xl:grid-cols-2 gap-4">
                    {config?.api_providers?.map((provider: any, idx: number) => (
                      <div key={idx} className="bg-white dark:bg-gray-900 border border-gray-200 dark:border-gray-800 rounded-xl p-4 shadow-sm relative group">
                        {config.api_providers.length > 1 && (
                          <button onClick={() => setConfig((prev: any) => { const newConfig = { ...prev }; newConfig.api_providers.splice(idx, 1); return newConfig; })} className="absolute top-3 right-3 p-1.5 text-gray-400 hover:text-red-500 hover:bg-red-50 dark:hover:bg-red-900/30 rounded transition-colors opacity-0 group-hover:opacity-100" title="删除供应商">
                            <Trash2 size={14} />
                          </button>
                        )}
                        <div className="grid grid-cols-2 gap-3 mb-3 pr-8">
                          <div className="space-y-1">
                            <label className="text-[11px] font-semibold text-gray-500 uppercase">供应商名称</label>
                            <input type="text" value={provider.name} onChange={(e) => updateProvider(idx, 'name', e.target.value)} className="w-full px-2.5 py-1.5 border border-gray-300 dark:border-gray-700 rounded-md text-sm bg-gray-50 dark:bg-gray-950 focus:ring-1 focus:ring-blue-500 outline-none" />
                          </div>
                          <div className="space-y-1">
                            <label className="text-[11px] font-semibold text-gray-500 uppercase">客户端类型</label>
                            <select value={provider.client_type} onChange={(e) => updateProvider(idx, 'client_type', e.target.value)} className="w-full px-2.5 py-1.5 border border-gray-300 dark:border-gray-700 rounded-md text-sm bg-gray-50 dark:bg-gray-950 focus:ring-1 focus:ring-blue-500 outline-none">
                              <option value="openai">OpenAI 兼容</option>
                              <option value="anthropic">Anthropic</option>
                              <option value="google">Google</option>
                            </select>
                          </div>
                        </div>
                        <div className="grid grid-cols-2 gap-3">
                          <div className="space-y-1">
                            <label className="text-[11px] font-semibold text-gray-500 uppercase">API Key</label>
                            <input type="password" value={provider.api_key} onChange={(e) => updateProvider(idx, 'api_key', e.target.value)} className="w-full px-2.5 py-1.5 border border-gray-300 dark:border-gray-700 rounded-md text-sm bg-gray-50 dark:bg-gray-950 focus:ring-1 focus:ring-blue-500 outline-none font-mono" placeholder="sk-..." />
                          </div>
                          <div className="space-y-1">
                            <label className="text-[11px] font-semibold text-gray-500 uppercase">Base URL (选填)</label>
                            <input type="text" value={provider.base_url} onChange={(e) => updateProvider(idx, 'base_url', e.target.value)} className="w-full px-2.5 py-1.5 border border-gray-300 dark:border-gray-700 rounded-md text-sm bg-gray-50 dark:bg-gray-950 focus:ring-1 focus:ring-blue-500 outline-none font-mono" placeholder="默认" />
                          </div>
                        </div>
                      </div>
                    ))}
                  </div>
                </div>

                {/* Models */}
                <div className="space-y-3 pt-4 border-t border-gray-100 dark:border-gray-800">
                  <div className="flex items-center justify-between">
                    <h3 className="text-sm font-semibold text-gray-900 dark:text-white">可用模型列表</h3>
                    <button onClick={() => setConfig((prev: any) => ({ ...prev, models: [...(prev.models || []), { model_id: 'new-model', api_provider: prev.api_providers?.[0]?.name || '' }] }))} className="flex items-center gap-1.5 text-xs text-blue-600 dark:text-blue-400 font-medium hover:underline px-2 bg-blue-50 dark:bg-blue-900/30 py-1 rounded">
                      <Plus size={14} /> 添加模型
                    </button>
                  </div>
                  <div className="bg-white dark:bg-gray-900 border border-gray-200 dark:border-gray-800 rounded-xl shadow-sm overflow-hidden text-sm">
                    <div className="grid grid-cols-13 bg-gray-50 dark:bg-gray-800/50 px-4 py-2 border-b border-gray-200 dark:border-gray-800 text-xs font-semibold text-gray-500 uppercase">
                      <div className="col-span-1"></div>
                      <div className="col-span-4">模型 ID</div>
                      <div className="col-span-3">服务商</div>
                      <div className="col-span-2">最大上下文</div>
                      <div className="col-span-2">价格 (入/出)</div>
                      <div className="col-span-1 text-right">操作</div>
                    </div>
                    {config?.models?.map((model: any, idx: number) => (
                      <React.Fragment key={idx}>
                        <div className="grid grid-cols-13 gap-3 px-4 py-2.5 items-center border-b border-gray-100 dark:border-gray-800 hover:bg-gray-50/50 dark:hover:bg-gray-800/30">
                          <div className="col-span-1">
                            <button onClick={() => setExpandedModel(expandedModel === idx ? null : idx)} className="p-1 text-gray-400 hover:text-gray-600 dark:hover:text-gray-300 rounded transition-colors">
                              {expandedModel === idx ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
                            </button>
                          </div>
                          <div className="col-span-4">
                            <input type="text" value={model.model_id} onChange={(e) => updateModel(idx, 'model_id', e.target.value)} className="w-full px-2 py-1 border border-transparent hover:border-gray-300 dark:hover:border-gray-700 focus:border-blue-500 rounded bg-transparent outline-none transition-colors" placeholder="gpt-4o" />
                          </div>
                          <div className="col-span-3">
                            <select value={model.api_provider} onChange={(e) => updateModel(idx, 'api_provider', e.target.value)} className="w-full px-2 py-1 border border-transparent hover:border-gray-300 dark:hover:border-gray-700 focus:border-blue-500 rounded bg-transparent outline-none transition-colors">
                              {config.api_providers?.map((p: any) => <option key={p.name} value={p.name}>{p.name}</option>)}
                            </select>
                          </div>
                          <div className="col-span-2">
                            <input type="text" value={model.max_context || ''} onChange={(e) => updateModel(idx, 'max_context', e.target.value)} className="w-full px-2 py-1 border border-transparent hover:border-gray-300 dark:hover:border-gray-700 focus:border-blue-500 rounded bg-transparent outline-none transition-colors" placeholder="如 128k" />
                          </div>
                          <div className="col-span-2 flex gap-2">
                            <input type="text" value={model.price_in ?? ''} onChange={(e) => updateModel(idx, 'price_in', e.target.value)} className="w-full px-2 py-1 border border-transparent hover:border-gray-300 dark:hover:border-gray-700 focus:border-blue-500 rounded bg-transparent outline-none transition-colors" placeholder="入" />
                            <input type="text" value={model.price_out ?? ''} onChange={(e) => updateModel(idx, 'price_out', e.target.value)} className="w-full px-2 py-1 border border-transparent hover:border-gray-300 dark:hover:border-gray-700 focus:border-blue-500 rounded bg-transparent outline-none transition-colors" placeholder="出" />
                          </div>
                          <div className="col-span-1 text-right">
                            <button onClick={() => setConfig((prev: any) => { const newConfig = { ...prev }; newConfig.models.splice(idx, 1); return newConfig; })} className="p-1.5 text-gray-400 hover:text-red-500 rounded hover:bg-red-50 dark:hover:bg-red-900/30 transition-colors">
                              <Trash2 size={15} />
                            </button>
                          </div>
                        </div>
                        {expandedModel === idx && (
                          <div className="px-10 py-3 bg-gray-50/50 dark:bg-gray-800/20 border-b border-gray-100 dark:border-gray-800 grid grid-cols-2 gap-4">
                            <label className="flex items-center gap-2 text-xs text-gray-600 dark:text-gray-400 cursor-pointer select-none">
                              <input type="checkbox" checked={model.force_stream_mode === true} onChange={(e) => updateModel(idx, 'force_stream_mode', e.target.checked)} className="rounded border-gray-300 dark:border-gray-600 text-blue-600 focus:ring-blue-500" />
                              强制流式模式
                            </label>
                            <label className="flex items-center gap-2 text-xs text-gray-600 dark:text-gray-400 cursor-pointer select-none">
                              <input type="checkbox" checked={model.tool_call_compat === true} onChange={(e) => updateModel(idx, 'tool_call_compat', e.target.checked)} className="rounded border-gray-300 dark:border-gray-600 text-blue-600 focus:ring-blue-500" />
                              工具调用兼容
                            </label>
                          </div>
                        )}
                      </React.Fragment>
                    ))}
                  </div>
                </div>

                {/* Roles */}
                <div className="space-y-3 pt-4 border-t border-gray-100 dark:border-gray-800">
                  <h3 className="text-sm font-semibold text-gray-900 dark:text-white">角色绑定 (Role Assignments)</h3>
                  <div className="bg-white dark:bg-gray-900 border border-gray-200 dark:border-gray-800 rounded-xl p-4 shadow-sm">
                    <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
                      {['main', 'coder', 'researcher', 'reviewer', 'title'].map((role) => (
                        <div key={role} className="flex flex-col space-y-1.5">
                          <label className="text-xs font-semibold text-gray-500 uppercase">{role}</label>
                          <select value={config?.roles?.[role] || ''} onChange={(e) => updateNestedConfig(['roles', role], e.target.value)} className="w-full px-2.5 py-1.5 border border-gray-300 dark:border-gray-700 rounded-md text-sm bg-gray-50 dark:bg-gray-950 focus:ring-1 focus:ring-blue-500 outline-none">
                            <option value="">-- 默认跟随 main --</option>
                            {config?.models?.map((m: any) => {
                              const name = `${m.api_provider}/${m.model_id}`;
                              return <option key={name} value={name}>{name}</option>;
                            })}
                          </select>
                        </div>
                      ))}
                    </div>
                  </div>
                </div>

                {/* Coder Profile */}
                <div className="space-y-3 pt-4 border-t border-gray-100 dark:border-gray-800">
                  <h3 className="text-sm font-semibold text-gray-900 dark:text-white">Coder 参数微调</h3>
                  <div className="bg-white dark:bg-gray-900 border border-gray-200 dark:border-gray-800 rounded-xl p-4 shadow-sm flex items-center gap-6">
                    <div className="flex-1">
                      <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">Temperature</label>
                      <p className="text-[11px] text-gray-500">控制 Coder 编写代码的随机性 (建议 0.0 - 0.2)</p>
                    </div>
                    <div className="flex items-center gap-3 w-64">
                      <input type="range" min="0" max="1" step="0.05" value={String(config?.model_profiles?.find((p: any) => p.profile_name === 'Coder')?.temperature || 0.2)} onChange={(e) => {
                        const val = parseFloat(e.target.value);
                        const idx = config?.model_profiles?.findIndex((p: any) => p.profile_name === 'Coder');
                        if (idx >= 0) {
                          updateNestedConfig(['model_profiles', String(idx), 'temperature'], val);
                        } else {
                          setConfig((prev: any) => ({ ...prev, model_profiles: [...(prev.model_profiles || []), { profile_name: 'Coder', temperature: val, max_tokens: 16384 }] }));
                        }
                      }} className="flex-1 h-1.5 bg-gray-200 dark:bg-gray-700 rounded-lg appearance-none cursor-pointer accent-blue-600" />
                      <span className="w-10 text-right text-sm font-mono font-medium text-blue-600 dark:text-blue-400">
                        {config?.model_profiles?.find((p: any) => p.profile_name === 'Coder')?.temperature || 0.2}
                      </span>
                    </div>
                  </div>
                </div>
              </div>
            )}

            {activeTab === 'mcp' && (
              <div className="space-y-5 animate-in fade-in slide-in-from-bottom-2 duration-300">
                <div className="flex items-center justify-between">
                  <div>
                    <h2 className="text-lg font-semibold text-gray-900 dark:text-white mb-1">MCP 服务器</h2>
                    <p className="text-sm text-gray-500 dark:text-gray-400">配置 Model Context Protocol 扩展工具端点。</p>
                  </div>
                  <button onClick={() => setConfig((prev: any) => ({ ...prev, mcp_servers: [...(prev.mcp_servers || []), { name: 'new-server', command: 'npx', args: ['-y', 'mcp-server'], enabled: true }] }))} className="flex items-center gap-1.5 px-3 py-1.5 bg-gray-900 dark:bg-white text-white dark:text-gray-900 text-sm font-medium rounded-lg hover:bg-gray-800 dark:hover:bg-gray-100 transition-colors">
                    <Plus size={16} /> 添加端点
                  </button>
                </div>

                <div className="space-y-3">
                  {config?.mcp_servers?.map((server: any, idx: number) => (
                    <div key={idx} className={`bg-white dark:bg-gray-900 border ${server.enabled !== false ? 'border-blue-200/50 dark:border-blue-900/30 shadow-sm' : 'border-gray-200 dark:border-gray-800 opacity-75'} rounded-xl p-4 transition-all flex flex-col gap-3 group relative`}>
                      
                      <button onClick={() => setConfig((prev: any) => { const newConfig = { ...prev }; newConfig.mcp_servers.splice(idx, 1); return newConfig; })} className="absolute top-3 right-3 p-1.5 text-gray-400 hover:text-red-500 rounded transition-colors opacity-0 group-hover:opacity-100 hover:bg-red-50 dark:hover:bg-red-900/20">
                        <Trash2 size={16} />
                      </button>

                      <div className="flex items-center gap-3 pr-8">
                        <label className="relative inline-flex items-center cursor-pointer shrink-0">
                          <input type="checkbox" className="sr-only peer" checked={server.enabled !== false} onChange={(e) => updateMcpServer(idx, 'enabled', e.target.checked)} />
                          <div className="w-9 h-5 bg-gray-200 peer-focus:outline-none rounded-full peer dark:bg-gray-700 peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:border-gray-300 after:border after:rounded-full after:h-4 after:w-4 after:transition-all dark:border-gray-600 peer-checked:bg-blue-600"></div>
                        </label>
                        <input type="text" value={server.name} onChange={(e) => updateMcpServer(idx, 'name', e.target.value)} className={`text-sm font-semibold bg-transparent outline-none focus:border-b border-gray-300 dark:border-gray-600 flex-1 ${server.enabled !== false ? 'text-gray-900 dark:text-white' : 'text-gray-500 line-through'}`} placeholder="服务器名称" />
                      </div>

                      <div className="grid grid-cols-12 gap-3">
                        <div className="col-span-3">
                          <label className="block text-[11px] font-semibold text-gray-500 uppercase mb-1">Command</label>
                          <input type="text" value={server.command} onChange={(e) => updateMcpServer(idx, 'command', e.target.value)} className="w-full px-2.5 py-1.5 border border-gray-300 dark:border-gray-700 rounded-md bg-gray-50 dark:bg-gray-950 text-sm focus:ring-1 focus:ring-blue-500 outline-none" placeholder="e.g. npx" disabled={server.enabled === false} />
                        </div>
                        <div className="col-span-9">
                          <label className="block text-[11px] font-semibold text-gray-500 uppercase mb-1">Args (空格分隔)</label>
                          <input type="text" value={Array.isArray(server.args) ? server.args.join(' ') : server.args} onChange={(e) => { const arr = e.target.value.split(' ').filter(Boolean); updateMcpServer(idx, 'args', arr); }} className="w-full px-2.5 py-1.5 border border-gray-300 dark:border-gray-700 rounded-md bg-gray-50 dark:bg-gray-950 text-sm font-mono focus:ring-1 focus:ring-blue-500 outline-none" placeholder="-y @modelcontextprotocol/server-postgres" disabled={server.enabled === false} />
                        </div>
                      </div>
                    </div>
                  ))}
                  
                  {(!config?.mcp_servers || config.mcp_servers.length === 0) && (
                    <div className="text-center py-10 border border-dashed border-gray-300 dark:border-gray-700 rounded-xl bg-gray-50/50 dark:bg-gray-900/20">
                      <p className="text-sm text-gray-500 dark:text-gray-400">暂未配置任何 MCP 服务器。</p>
                    </div>
                  )}
                </div>
              </div>
            )}

            {activeTab === 'advanced' && (
              <div className="space-y-5 animate-in fade-in slide-in-from-bottom-2 duration-300">
                <div>
                  <h2 className="text-lg font-semibold text-gray-900 dark:text-white mb-1">高级设置</h2>
                  <p className="text-sm text-gray-500 dark:text-gray-400">微调模型全局推理参数。</p>
                </div>

                <div className="bg-white dark:bg-gray-900 border border-gray-200 dark:border-gray-800 rounded-xl p-5 shadow-sm space-y-5">
                  <div className="flex items-center gap-6">
                    <div className="flex-1">
                      <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">全局 Temperature</label>
                      <p className="text-[11px] text-gray-500">控制回答的随机性和创造性 (建议 0.0 - 0.5)</p>
                    </div>
                    <div className="flex items-center gap-3 w-64">
                      <input type="range" min="0" max="2" step="0.1" value={String(config?.model_profiles?.[0]?.temperature || 0.5)} onChange={(e) => updateNestedConfig(['model_profiles', '0', 'temperature'], parseFloat(e.target.value))} className="flex-1 h-1.5 bg-gray-200 dark:bg-gray-700 rounded-lg appearance-none cursor-pointer accent-blue-600" />
                      <span className="w-10 text-right text-sm font-mono font-medium text-blue-600 dark:text-blue-400">
                        {config?.model_profiles?.[0]?.temperature || 0.5}
                      </span>
                    </div>
                  </div>
                  
                  <div className="w-full h-px bg-gray-100 dark:bg-gray-800" />

                  <div className="flex items-center gap-6">
                    <div className="flex-1">
                      <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">全局 Max Tokens</label>
                      <p className="text-[11px] text-gray-500">单次回复生成的最大 Token 数量</p>
                    </div>
                    <div className="w-64">
                      <input type="number" value={String(config?.model_profiles?.[0]?.max_tokens || 16384)} onChange={(e) => updateNestedConfig(['model_profiles', '0', 'max_tokens'], parseInt(e.target.value))} className="w-full px-3 py-1.5 border border-gray-300 dark:border-gray-700 rounded-md bg-gray-50 dark:bg-gray-950 text-sm focus:ring-1 focus:ring-blue-500 outline-none font-mono" />
                    </div>
                  </div>

                  <div className="w-full h-px bg-gray-100 dark:bg-gray-800" />

                  {/* Coding Agent 设置 */}
                  <div>
                    <h3 className="text-sm font-semibold text-gray-900 dark:text-white mb-3">Coding Agent 设置</h3>
                    <p className="text-[11px] text-gray-500 mb-4">配置内置编码助手插件的行为参数。</p>
                    <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                      <div className="space-y-1.5">
                        <label className="text-xs font-medium text-gray-700 dark:text-gray-300">用户称呼</label>
                        <input type="text" value={config?.coding_agent?.tui_username || 'User'} onChange={(e) => updateCodingAgent('tui_username', e.target.value)} className="w-full px-3 py-2 border border-gray-300 dark:border-gray-700 rounded-lg text-sm bg-gray-50 dark:bg-gray-950 focus:ring-1 focus:ring-blue-500 outline-none" placeholder="User" />
                      </div>
                      <div className="space-y-1.5">
                        <label className="text-xs font-medium text-gray-700 dark:text-gray-300">首选终端环境</label>
                        <select value={config?.coding_agent?.preferred_terminal || ''} onChange={(e) => updateCodingAgent('preferred_terminal', e.target.value)} className="w-full px-3 py-2 border border-gray-300 dark:border-gray-700 rounded-lg text-sm bg-gray-50 dark:bg-gray-950 focus:ring-1 focus:ring-blue-500 outline-none">
                          <option value="">自动检测</option>
                          <option value="powershell">PowerShell 5</option>
                          <option value="pwsh">PowerShell 7</option>
                          <option value="cmd">CMD</option>
                          <option value="bash">Bash</option>
                        </select>
                      </div>
                      <div className="space-y-1.5">
                        <label className="text-xs font-medium text-gray-700 dark:text-gray-300">最大并行研究员数</label>
                        <input type="number" min={1} max={20} value={config?.coding_agent?.max_parallel_researchers || 6} onChange={(e) => updateCodingAgent('max_parallel_researchers', parseInt(e.target.value) || 6)} className="w-full px-3 py-2 border border-gray-300 dark:border-gray-700 rounded-lg text-sm bg-gray-50 dark:bg-gray-950 focus:ring-1 focus:ring-blue-500 outline-none" />
                      </div>
                      <div className="space-y-1.5">
                        <label className="text-xs font-medium text-gray-700 dark:text-gray-300">缓存有效期（小时）</label>
                        <input type="number" min={1} max={720} value={config?.coding_agent?.cache_ttl_hours || 24} onChange={(e) => updateCodingAgent('cache_ttl_hours', parseInt(e.target.value) || 24)} className="w-full px-3 py-2 border border-gray-300 dark:border-gray-700 rounded-lg text-sm bg-gray-50 dark:bg-gray-950 focus:ring-1 focus:ring-blue-500 outline-none" />
                      </div>
                    </div>
                  </div>
                </div>
              </div>
            )}

            {activeTab === 'about' && (
              <div className="space-y-5 animate-in fade-in slide-in-from-bottom-2 duration-300">
                <div>
                  <h2 className="text-lg font-semibold text-gray-900 dark:text-white mb-1">关于 MoFox Code</h2>
                  <p className="text-sm text-gray-500 dark:text-gray-400">版本信息与组件清单</p>
                </div>
                <div className="bg-gray-50 dark:bg-gray-900 rounded-xl border border-gray-200 dark:border-gray-800 overflow-hidden">
                  <table className="w-full text-sm">
                    <tbody>
                      {[
                        { label: 'MoFox Code Desktop', key: 'desktop' },
                        { label: 'Neo-MoFox 框架', key: 'framework' },
                        { label: 'coding_agent 插件', key: 'coding_agent' },
                        { label: 'coding_agent_webui 插件', key: 'coding_agent_webui' },
                      ].map(({ label, key }) => (
                        <tr key={key} className="border-b border-gray-200 dark:border-gray-800 last:border-b-0">
                          <td className="px-4 py-3 text-gray-700 dark:text-gray-300 font-medium">{label}</td>
                          <td className="px-4 py-3 text-gray-500 dark:text-gray-400 font-mono text-xs">
                            {version?.[key] ?? <RefreshCw className="animate-spin inline w-3 h-3" />}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            )}

          </div>
        </div>

        {/* 底部保存条 */}
        <div className="p-4 border-t border-gray-200 dark:border-gray-800 bg-white dark:bg-gray-900 flex items-center justify-end gap-3 shrink-0 shadow-[0_-4px_6px_-1px_rgba(0,0,0,0.02)] z-10">
          {error && <span className="text-red-500 text-sm font-medium flex items-center mr-auto px-2">{error}</span>}
          {saveSuccess && <span className="text-green-600 dark:text-green-400 text-sm font-medium flex items-center mr-auto px-2 animate-in fade-in zoom-in duration-200">✓ 配置已保存，部分修改需重启生效</span>}
          <button onClick={onClose} className="px-5 py-2 rounded-lg text-sm font-medium text-gray-600 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-800 transition-colors">
            取消
          </button>
          <button onClick={handleSave} disabled={saving || saveSuccess} className="px-5 py-2 rounded-lg text-sm font-medium bg-blue-600 text-white hover:bg-blue-700 shadow-sm shadow-blue-500/20 disabled:opacity-50 disabled:cursor-not-allowed transition-all">
            {saving ? '保存中...' : saveSuccess ? '已保存' : '保存更改并重启'}
          </button>
        </div>
      </div>
    </div>
  );
};

export default SettingsModal;
