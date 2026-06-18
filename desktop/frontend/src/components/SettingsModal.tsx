import React, { useState, useEffect } from 'react';
import { User, Cpu, Network, RefreshCw, Plus, Trash2, Settings as SettingsIcon } from 'lucide-react';

interface SettingsModalProps {
  port: number;
  onClose: () => void;
}

const SettingsModal: React.FC<SettingsModalProps> = ({ port, onClose }) => {
  const [activeTab, setActiveTab] = useState('general');
  const [config, setConfig] = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');

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
    try {
      const res = await fetch(`http://127.0.0.1:${port}/api/settings`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(config)
      });
      const data = await res.json();
      if (data.status === 'error') {
        setError(data.message);
      } else {
        onClose();
        // 重启或提示刷新 (后端生成配置后可能需要重启)
        alert('配置已保存！某些修改可能需要重启应用程序才能生效。');
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

  const updateModel = (index: number, field: string, value: string) => {
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

  if (loading) {
    return <div className="flex items-center justify-center h-full"><RefreshCw className="animate-spin text-gray-400" /></div>;
  }

  if (error && !config) {
    return <div className="p-8 text-center text-red-500">{error}</div>;
  }

  const tabs = [
    { id: 'general', label: '常规', icon: User },
    { id: 'models', label: '模型与 API', icon: Cpu },
    { id: 'mcp', label: 'MCP 服务器', icon: Network },
    { id: 'advanced', label: '高级设置', icon: SettingsIcon },
  ];

  return (
    <div className="flex h-full bg-white dark:bg-gray-950">
      {/* 侧边栏 */}
      <div className="w-64 border-r border-gray-200 dark:border-gray-800 bg-gray-50 dark:bg-gray-900/50 flex flex-col shrink-0">
        <div className="p-4 pt-6">
          <h3 className="text-xs font-semibold text-gray-500 dark:text-gray-400 uppercase tracking-wider mb-3">设置类别</h3>
          <div className="space-y-1">
            {tabs.map(tab => (
              <button
                key={tab.id}
                onClick={() => setActiveTab(tab.id)}
                className={`w-full flex items-center gap-3 px-3 py-2 text-sm font-medium rounded-lg transition-colors ${
                  activeTab === tab.id
                    ? 'bg-blue-50 text-blue-700 dark:bg-blue-500/10 dark:text-blue-400'
                    : 'text-gray-700 dark:text-gray-300 hover:bg-gray-200/50 dark:hover:bg-gray-800'
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
        <div className="flex-1 overflow-y-auto p-8 relative">
          <div className="max-w-3xl mx-auto space-y-8 pb-10">
            
            {activeTab === 'general' && (
              <div className="space-y-6 animate-in fade-in slide-in-from-bottom-2 duration-300">
                <div>
                  <h2 className="text-xl font-semibold text-gray-900 dark:text-white mb-1">常规设置</h2>
                  <p className="text-sm text-gray-500 dark:text-gray-400">调整助手的基础属性和行为习惯。</p>
                </div>
                
                <div className="space-y-4 bg-white dark:bg-gray-900 border border-gray-200 dark:border-gray-800 rounded-xl p-5 shadow-sm">
                  <div>
                    <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">AI 昵称</label>
                    <input
                      type="text"
                      value={config?.personality?.nickname || ''}
                      onChange={(e) => updateNestedConfig(['personality', 'nickname'], e.target.value)}
                      className="w-full px-3 py-2 border border-gray-300 dark:border-gray-700 rounded-lg bg-transparent text-gray-900 dark:text-white focus:ring-2 focus:ring-blue-500 focus:border-transparent outline-none transition-all"
                      placeholder="例如: 小狐狸"
                    />
                  </div>
                  <div>
                    <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">回复风格</label>
                    <input
                      type="text"
                      value={config?.personality?.reply_style || ''}
                      onChange={(e) => updateNestedConfig(['personality', 'reply_style'], e.target.value)}
                      className="w-full px-3 py-2 border border-gray-300 dark:border-gray-700 rounded-lg bg-transparent text-gray-900 dark:text-white focus:ring-2 focus:ring-blue-500 focus:border-transparent outline-none transition-all"
                      placeholder="例如: 自然口语化"
                    />
                  </div>
                  <div>
                    <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">身份设定 (System Prompt)</label>
                    <textarea
                      value={config?.personality?.identity || ''}
                      onChange={(e) => updateNestedConfig(['personality', 'identity'], e.target.value)}
                      rows={4}
                      className="w-full px-3 py-2 border border-gray-300 dark:border-gray-700 rounded-lg bg-transparent text-gray-900 dark:text-white focus:ring-2 focus:ring-blue-500 focus:border-transparent outline-none transition-all resize-y"
                      placeholder="描述 AI 的背景和基础设定"
                    />
                  </div>
                </div>
              </div>
            )}

            {activeTab === 'models' && (
              <div className="space-y-6 animate-in fade-in slide-in-from-bottom-2 duration-300">
                <div>
                  <h2 className="text-xl font-semibold text-gray-900 dark:text-white mb-1">模型与 API</h2>
                  <p className="text-sm text-gray-500 dark:text-gray-400">配置您的大型语言模型 API 密钥及默认调度模型。</p>
                </div>

                {/* Providers */}
                <div className="space-y-3">
                  <h3 className="text-sm font-semibold text-gray-900 dark:text-white">API 供应商 (Providers)</h3>
                  {config?.api_providers?.map((provider: any, idx: number) => (
                    <div key={idx} className="bg-white dark:bg-gray-900 border border-gray-200 dark:border-gray-800 rounded-xl p-5 shadow-sm space-y-4">
                      <div className="flex justify-between items-center mb-1">
                        <span className="text-xs font-semibold text-gray-500 uppercase">Provider #{idx + 1}</span>
                        {config.api_providers.length > 1 && (
                          <button
                            onClick={() => setConfig((prev: any) => {
                              const newConfig = { ...prev };
                              newConfig.api_providers.splice(idx, 1);
                              return newConfig;
                            })}
                            className="p-1 text-gray-400 hover:text-red-500 rounded transition-colors"
                            title="删除供应商"
                          >
                            <Trash2 size={16} />
                          </button>
                        )}
                      </div>
                      <div className="grid grid-cols-2 gap-4">
                        <div>
                          <label className="block text-xs font-medium text-gray-500 mb-1">供应商名称 (唯一标识)</label>
                          <input
                            type="text"
                            value={provider.name}
                            onChange={(e) => updateProvider(idx, 'name', e.target.value)}
                            className="w-full px-3 py-1.5 border border-gray-300 dark:border-gray-700 rounded-lg bg-transparent text-gray-900 dark:text-white text-sm"
                          />
                        </div>
                        <div>
                          <label className="block text-xs font-medium text-gray-500 mb-1">客户端类型</label>
                          <select
                            value={provider.client_type}
                            onChange={(e) => updateProvider(idx, 'client_type', e.target.value)}
                            className="w-full px-3 py-1.5 border border-gray-300 dark:border-gray-700 rounded-lg bg-transparent text-gray-900 dark:text-white text-sm"
                          >
                            <option value="openai">OpenAI 兼容</option>
                            <option value="anthropic">Anthropic</option>
                            <option value="google">Google</option>
                          </select>
                        </div>
                      </div>
                      <div>
                        <label className="block text-xs font-medium text-gray-500 mb-1">API Key</label>
                        <input
                          type="password"
                          value={provider.api_key}
                          onChange={(e) => updateProvider(idx, 'api_key', e.target.value)}
                          className="w-full px-3 py-1.5 border border-gray-300 dark:border-gray-700 rounded-lg bg-transparent text-gray-900 dark:text-white text-sm"
                          placeholder="sk-..."
                        />
                      </div>
                      <div>
                        <label className="block text-xs font-medium text-gray-500 mb-1">Base URL (选填)</label>
                        <input
                          type="text"
                          value={provider.base_url}
                          onChange={(e) => updateProvider(idx, 'base_url', e.target.value)}
                          className="w-full px-3 py-1.5 border border-gray-300 dark:border-gray-700 rounded-lg bg-transparent text-gray-900 dark:text-white text-sm"
                          placeholder="自定义 API 地址"
                        />
                      </div>
                    </div>
                  ))}
                  <button 
                    onClick={() => setConfig((prev: any) => ({
                      ...prev,
                      api_providers: [...prev.api_providers, { name: 'new_provider', client_type: 'openai', api_key: '', base_url: '' }]
                    }))}
                    className="flex items-center gap-2 text-sm text-blue-600 dark:text-blue-400 font-medium hover:underline px-2"
                  >
                    <Plus size={16} /> 添加 API 供应商
                  </button>
                </div>

                {/* Models */}
                <div className="space-y-3 pt-4 border-t border-gray-200 dark:border-gray-800">
                  <h3 className="text-sm font-semibold text-gray-900 dark:text-white">可用模型 (Models)</h3>
                  <div className="bg-white dark:bg-gray-900 border border-gray-200 dark:border-gray-800 rounded-xl shadow-sm overflow-hidden">
                    {config?.models?.map((model: any, idx: number) => (
                      <div key={idx} className="flex items-center gap-3 p-3 border-b border-gray-100 dark:border-gray-800 last:border-0">
                        <input
                          type="text"
                          value={model.model_id}
                          onChange={(e) => updateModel(idx, 'model_id', e.target.value)}
                          className="flex-1 px-3 py-1.5 border border-gray-300 dark:border-gray-700 rounded-lg bg-transparent text-gray-900 dark:text-white text-sm"
                          placeholder="例如: gpt-4o"
                        />
                        <span className="text-gray-400 text-sm">@</span>
                        <select
                          value={model.api_provider}
                          onChange={(e) => updateModel(idx, 'api_provider', e.target.value)}
                          className="w-40 px-3 py-1.5 border border-gray-300 dark:border-gray-700 rounded-lg bg-transparent text-gray-900 dark:text-white text-sm"
                        >
                          {config.api_providers?.map((p: any) => (
                            <option key={p.name} value={p.name}>{p.name}</option>
                          ))}
                        </select>
                        <button
                          onClick={() => setConfig((prev: any) => {
                            const newConfig = { ...prev };
                            newConfig.models.splice(idx, 1);
                            return newConfig;
                          })}
                          className="p-1.5 text-gray-400 hover:text-red-500 rounded transition-colors"
                        >
                          <Trash2 size={16} />
                        </button>
                      </div>
                    ))}
                    <div className="p-3 bg-gray-50/50 dark:bg-gray-900/50">
                      <button 
                        onClick={() => setConfig((prev: any) => ({
                          ...prev,
                          models: [...(prev.models || []), { model_id: 'new-model', api_provider: prev.api_providers?.[0]?.name || '' }]
                        }))}
                        className="flex items-center gap-2 text-sm text-gray-600 dark:text-gray-400 font-medium hover:text-gray-900 dark:hover:text-white transition-colors"
                      >
                        <Plus size={16} /> 添加模型
                      </button>
                    </div>
                  </div>
                </div>

                {/* Roles */}
                <div className="space-y-3 pt-4 border-t border-gray-200 dark:border-gray-800">
                  <h3 className="text-sm font-semibold text-gray-900 dark:text-white">角色绑定 (Role Assignments)</h3>
                  <div className="bg-white dark:bg-gray-900 border border-gray-200 dark:border-gray-800 rounded-xl p-5 shadow-sm space-y-4">
                    <p className="text-xs text-gray-500 mb-4">选择 MoFox Code 在不同工作环节中默认使用的模型。</p>
                    {['main', 'coder', 'researcher'].map((role) => (
                      <div key={role} className="flex items-center justify-between">
                        <label className="text-sm font-medium text-gray-700 dark:text-gray-300 capitalize w-24">{role}</label>
                        <select
                          value={config?.roles?.[role] || ''}
                          onChange={(e) => updateNestedConfig(['roles', role], e.target.value)}
                          className="flex-1 px-3 py-1.5 border border-gray-300 dark:border-gray-700 rounded-lg bg-transparent text-gray-900 dark:text-white text-sm"
                        >
                          <option value="">-- 选择模型 --</option>
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
            )}

            {activeTab === 'mcp' && (
              <div className="space-y-6 animate-in fade-in slide-in-from-bottom-2 duration-300">
                <div className="flex items-center justify-between">
                  <div>
                    <h2 className="text-xl font-semibold text-gray-900 dark:text-white mb-1">MCP 服务器</h2>
                    <p className="text-sm text-gray-500 dark:text-gray-400">配置 Model Context Protocol 以扩展 AI 的能力。</p>
                  </div>
                  <button 
                    onClick={() => setConfig((prev: any) => ({
                      ...prev,
                      mcp_servers: [...(prev.mcp_servers || []), { name: 'new-server', command: 'npx', args: ['-y', 'mcp-server'], enabled: true }]
                    }))}
                    className="flex items-center gap-2 px-3 py-1.5 bg-gray-900 dark:bg-white text-white dark:text-gray-900 text-sm font-medium rounded-lg hover:bg-gray-800 dark:hover:bg-gray-100 transition-colors"
                  >
                    <Plus size={16} /> 添加
                  </button>
                </div>

                <div className="space-y-4">
                  {config?.mcp_servers?.map((server: any, idx: number) => (
                    <div key={idx} className={`bg-white dark:bg-gray-900 border ${server.enabled ? 'border-blue-200 dark:border-blue-900/50 shadow-sm' : 'border-gray-200 dark:border-gray-800 opacity-60'} rounded-xl p-5 transition-all`}>
                      <div className="flex items-start justify-between mb-4">
                        <div className="flex items-center gap-3">
                          <label className="relative inline-flex items-center cursor-pointer">
                            <input 
                              type="checkbox" 
                              className="sr-only peer" 
                              checked={server.enabled !== false}
                              onChange={(e) => updateMcpServer(idx, 'enabled', e.target.checked)}
                            />
                            <div className="w-9 h-5 bg-gray-200 peer-focus:outline-none rounded-full peer dark:bg-gray-700 peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:border-gray-300 after:border after:rounded-full after:h-4 after:w-4 after:transition-all dark:border-gray-600 peer-checked:bg-blue-600"></div>
                          </label>
                          <input
                            type="text"
                            value={server.name}
                            onChange={(e) => updateMcpServer(idx, 'name', e.target.value)}
                            className="text-base font-semibold bg-transparent text-gray-900 dark:text-white outline-none focus:border-b border-gray-300 dark:border-gray-700"
                            placeholder="服务器名称"
                          />
                        </div>
                        <button
                          onClick={() => setConfig((prev: any) => {
                            const newConfig = { ...prev };
                            newConfig.mcp_servers.splice(idx, 1);
                            return newConfig;
                          })}
                          className="p-1.5 text-gray-400 hover:text-red-500 rounded transition-colors"
                        >
                          <Trash2 size={18} />
                        </button>
                      </div>

                      <div className="grid grid-cols-12 gap-3">
                        <div className="col-span-3">
                          <label className="block text-xs font-medium text-gray-500 mb-1">Command</label>
                          <input
                            type="text"
                            value={server.command}
                            onChange={(e) => updateMcpServer(idx, 'command', e.target.value)}
                            className="w-full px-3 py-1.5 border border-gray-300 dark:border-gray-700 rounded-lg bg-gray-50 dark:bg-gray-950 text-gray-900 dark:text-white text-sm"
                            placeholder="e.g. npx"
                          />
                        </div>
                        <div className="col-span-9">
                          <label className="block text-xs font-medium text-gray-500 mb-1">Args (空格分隔)</label>
                          <input
                            type="text"
                            value={Array.isArray(server.args) ? server.args.join(' ') : server.args}
                            onChange={(e) => {
                              const arr = e.target.value.split(' ').filter(Boolean);
                              updateMcpServer(idx, 'args', arr);
                            }}
                            className="w-full px-3 py-1.5 border border-gray-300 dark:border-gray-700 rounded-lg bg-gray-50 dark:bg-gray-950 text-gray-900 dark:text-white text-sm font-mono"
                            placeholder="-y @modelcontextprotocol/server-postgres"
                          />
                        </div>
                      </div>
                    </div>
                  ))}
                  
                  {(!config?.mcp_servers || config.mcp_servers.length === 0) && (
                    <div className="text-center py-12 border-2 border-dashed border-gray-200 dark:border-gray-800 rounded-xl">
                      <p className="text-gray-500 dark:text-gray-400">暂未配置任何 MCP 服务器。</p>
                    </div>
                  )}
                </div>
              </div>
            )}

            {activeTab === 'advanced' && (
              <div className="space-y-6 animate-in fade-in slide-in-from-bottom-2 duration-300">
                <div>
                  <h2 className="text-xl font-semibold text-gray-900 dark:text-white mb-1">高级设置</h2>
                  <p className="text-sm text-gray-500 dark:text-gray-400">微调模型推理参数和全局设定。</p>
                </div>

                <div className="bg-white dark:bg-gray-900 border border-gray-200 dark:border-gray-800 rounded-xl p-5 shadow-sm space-y-6">
                  <div>
                    <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">Temperature</label>
                    <p className="text-xs text-gray-500 mb-3">控制回答的随机性和创造性 (建议编程任务使用 0.0 - 0.5)</p>
                    <div className="flex items-center gap-4">
                      <input
                        type="range"
                        min="0"
                        max="2"
                        step="0.1"
                        value={String(config?.model_profiles?.[0]?.temperature || 0.5)}
                        onChange={(e) => updateNestedConfig(['model_profiles', '0', 'temperature'], parseFloat(e.target.value))}
                        className="flex-1 accent-blue-600"
                      />
                      <span className="w-12 text-right text-sm font-mono text-gray-600 dark:text-gray-400">
                        {config?.model_profiles?.[0]?.temperature || 0.5}
                      </span>
                    </div>
                  </div>

                  <div>
                    <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">Max Tokens</label>
                    <p className="text-xs text-gray-500 mb-3">单次回复生成的最大 Token 数量</p>
                    <input
                      type="number"
                      value={String(config?.model_profiles?.[0]?.max_tokens || 16384)}
                      onChange={(e) => updateNestedConfig(['model_profiles', '0', 'max_tokens'], parseInt(e.target.value))}
                      className="w-full px-3 py-2 border border-gray-300 dark:border-gray-700 rounded-lg bg-transparent text-gray-900 dark:text-white focus:ring-2 focus:ring-blue-500 focus:border-transparent outline-none transition-all"
                    />
                  </div>
                </div>
              </div>
            )}

          </div>
        </div>

        {/* 底部保存条 */}
        <div className="p-4 border-t border-gray-200 dark:border-gray-800 bg-gray-50 dark:bg-gray-900 flex justify-end gap-3 shrink-0">
          {error && <span className="text-red-500 text-sm flex items-center mr-auto px-4">{error}</span>}
          <button
            onClick={onClose}
            className="px-5 py-2 rounded-lg text-sm font-medium text-gray-700 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-800 transition-colors"
          >
            取消
          </button>
          <button
            onClick={handleSave}
            disabled={saving}
            className="px-5 py-2 rounded-lg text-sm font-medium bg-blue-600 text-white hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
          >
            {saving ? '保存中...' : '保存更改并重启'}
          </button>
        </div>
      </div>
    </div>
  );
};

export default SettingsModal;
