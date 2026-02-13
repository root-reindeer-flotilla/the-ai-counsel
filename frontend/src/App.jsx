import { useState, useEffect, useRef } from 'react';
import Sidebar from './components/Sidebar';
import ChatInterface from './components/ChatInterface';
import Settings from './components/Settings';
import { api } from './api';
import './App.css';
import './components/StageCopyButtons.css';

const ACTIVE_RUNS_STORAGE_KEY = 'llmcp_active_runs';
const CURRENT_CONVERSATION_STORAGE_KEY = 'llmcp_current_conversation_id';

function App() {
  const [conversations, setConversations] = useState([]);
  const [currentConversationId, setCurrentConversationId] = useState(null);
  const [currentConversation, setCurrentConversation] = useState(null);
  const [isLoading, setIsLoading] = useState(false);
  const [showSettings, setShowSettings] = useState(false);
  const [settingsInitialSection, setSettingsInitialSection] = useState('llm_keys');
  const [ollamaStatus, setOllamaStatus] = useState({
    connected: false,
    lastConnected: null,
    testing: false
  });
  const [councilConfigured, setCouncilConfigured] = useState(true); // Assume configured until checked
  const [councilModels, setCouncilModels] = useState([]);
  const [chairmanModel, setChairmanModel] = useState(null);
  const [searchProvider, setSearchProvider] = useState('duckduckgo');
  const [executionMode, setExecutionMode] = useState('full');
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [activeRuns, setActiveRuns] = useState(() => {
    try {
      return JSON.parse(sessionStorage.getItem(ACTIVE_RUNS_STORAGE_KEY) || '{}');
    } catch {
      return {};
    }
  });
  const abortControllerRef = useRef(null);
  const requestIdRef = useRef(0);
  const isInitialMount = useRef(true);

  useEffect(() => {
    sessionStorage.setItem(ACTIVE_RUNS_STORAGE_KEY, JSON.stringify(activeRuns));
  }, [activeRuns]);

  useEffect(() => {
    if (currentConversationId) {
      sessionStorage.setItem(CURRENT_CONVERSATION_STORAGE_KEY, currentConversationId);
    }
  }, [currentConversationId]);

  // Check initial configuration on mount
  useEffect(() => {
    checkInitialSetup();
  }, []);

  const checkInitialSetup = async () => {
    try {
      // 1. Get Settings to check for API keys
      const settings = await api.getSettings();

      // Load execution mode preference
      setExecutionMode(settings.execution_mode || 'full');
      setSearchProvider(settings.search_provider || 'duckduckgo');

      const hasApiKey = settings.openrouter_api_key_set ||
        settings.requesty_api_key_set ||
        settings.groq_api_key_set ||
        settings.openai_api_key_set ||
        settings.anthropic_api_key_set ||
        settings.google_api_key_set ||
        settings.mistral_api_key_set ||
        settings.deepseek_api_key_set;

      // 2. Test Ollama Connection
      // We do this regardless to update the status indicator
      const ollamaUrl = settings.ollama_base_url || 'http://localhost:11434';
      setOllamaStatus(prev => ({ ...prev, testing: true }));

      let isOllamaConnected = false;
      try {
        const result = await api.testOllamaConnection(ollamaUrl);
        isOllamaConnected = result.success;

        if (result.success) {
          setOllamaStatus({
            connected: true,
            lastConnected: new Date().toLocaleString(),
            testing: false
          });
        } else {
          setOllamaStatus({ connected: false, lastConnected: null, testing: false });
        }
      } catch (err) {
        console.error('Ollama initial test failed:', err);
        setOllamaStatus({ connected: false, lastConnected: null, testing: false });
      }

      // 3. Check if council is configured (has models selected)
      const models = settings.council_models || [];
      const chairman = settings.chairman_model || '';

      setCouncilModels(models);
      setChairmanModel(chairman);

      const hasCouncilMembers = models.some(m => m && m.trim() !== '');
      const hasChairman = chairman && chairman.trim() !== '';
      setCouncilConfigured(hasCouncilMembers && hasChairman);

      // 4. If no providers are configured, open settings
      if (!hasApiKey && !isOllamaConnected) {
        setShowSettings(true);
      }

    } catch (error) {
      console.error('Failed to check initial setup:', error);
    }
  };

  // Re-check council configuration when settings close
  const handleSettingsClose = async () => {
    setShowSettings(false);
    try {
      const settings = await api.getSettings();
      const models = settings.council_models || [];
      const chairman = settings.chairman_model || '';

      setCouncilModels(models);
      setChairmanModel(chairman);
      setSearchProvider(settings.search_provider || 'duckduckgo');

      const hasCouncilMembers = models.some(m => m && m.trim() !== '');
      const hasChairman = chairman && chairman.trim() !== '';
      setCouncilConfigured(hasCouncilMembers && hasChairman);
    } catch (error) {
      console.error('Error after closing settings:', error);
    }
  };

  const handleOpenSettings = (section = 'council') => {
    setSettingsInitialSection(section || 'council');
    setShowSettings(true);
  };

  // Load conversations on mount
  useEffect(() => {
    loadConversations();
  }, []);

  // Auto-save execution mode preference when changed
  useEffect(() => {
    // Skip saving on initial mount
    if (isInitialMount.current) {
      isInitialMount.current = false;
      return;
    }

    const saveExecutionMode = async () => {
      try {
        await api.updateSettings({ execution_mode: executionMode });
      } catch (error) {
        console.error('Failed to save execution mode:', error);
      }
    };

    saveExecutionMode();
  }, [executionMode]);

  const testOllamaConnection = async (customUrl = null) => {
    try {
      setOllamaStatus(prev => ({ ...prev, testing: true }));

      // Use custom URL if provided, otherwise get from settings
      let urlToTest = customUrl;
      if (!urlToTest) {
        const settings = await api.getSettings();
        urlToTest = settings.ollama_base_url;
      }

      if (!urlToTest) {
        setOllamaStatus({ connected: false, lastConnected: null, testing: false });
        return;
      }

      const result = await api.testOllamaConnection(urlToTest);

      if (result.success) {
        setOllamaStatus({
          connected: true,
          lastConnected: new Date().toLocaleString(),
          testing: false
        });
      } else {
        setOllamaStatus({ connected: false, lastConnected: null, testing: false });
      }
    } catch (error) {
      console.error('Ollama connection test failed:', error);
      setOllamaStatus({ connected: false, lastConnected: null, testing: false });
    }
  };

  // Load conversation details when selected
  useEffect(() => {
    if (currentConversationId) {
      if (abortControllerRef.current) {
        abortControllerRef.current.abort();
        abortControllerRef.current = null;
      }
      loadConversation(currentConversationId);
    }
  }, [currentConversationId]);

  const loadConversations = async (retryCount = 0) => {
    try {
      const convs = await api.listConversations();
      setConversations(convs);
      if (!currentConversationId) {
        const storedConversationId = sessionStorage.getItem(CURRENT_CONVERSATION_STORAGE_KEY);
        if (storedConversationId && convs.some((c) => c.id === storedConversationId)) {
          setCurrentConversationId(storedConversationId);
        }
      }
    } catch (error) {
      console.error('Failed to load conversations:', error);
      // Retry up to 3 times with increasing delays (1s, 2s, 3s)
      if (retryCount < 3) {
        setTimeout(() => loadConversations(retryCount + 1), (retryCount + 1) * 1000);
      }
    }
  };

  const loadConversation = async (id) => {
    try {
      const conv = await api.getConversation(id);
      setCurrentConversation(conv);

      const active = await api.getActiveRun(id);
      const activeRun = active?.active_run;
      if (!activeRun) {
        setIsLoading(false);
        setActiveRuns((prev) => {
          if (!prev[id]) return prev;
          const next = { ...prev };
          delete next[id];
          return next;
        });
        return;
      }

      setActiveRuns((prev) => ({ ...prev, [id]: activeRun.run_id }));
      setIsLoading(true);

      if (!activeRun.assistant_message_saved) {
        const isRunning = activeRun.status === 'running' || activeRun.status === 'queued';
        const stage1Done = Array.isArray(activeRun.stage1_results) && activeRun.stage1_results.length > 0;
        const stage2Done = Array.isArray(activeRun.stage2_results) && activeRun.stage2_results.length > 0;
        const assistantMsg = {
          role: 'assistant',
          stage1: activeRun.stage1_results || null,
          stage2: activeRun.stage2_results || null,
          stage3: activeRun.stage3_result || null,
          metadata: activeRun.metadata || null,
          loading: {
            search: false,
            stage1: isRunning && !stage1Done,
            stage2: isRunning && activeRun.execution_mode !== 'chat_only' && stage1Done && !stage2Done,
            stage3: isRunning && activeRun.execution_mode === 'full' && stage2Done && !activeRun.stage3_result,
          },
          timers: {
            stage1Start: null,
            stage1End: null,
            stage2Start: null,
            stage2End: null,
            stage3Start: null,
            stage3End: null,
          },
          progress: activeRun.progress || {
            stage1: { count: 0, total: 0, currentModel: null },
            stage2: { count: 0, total: 0, currentModel: null },
          }
        };

        setCurrentConversation((prev) => {
          if (!prev || prev.id !== id) return prev;
          const last = prev.messages[prev.messages.length - 1];
          const hasPendingAssistant =
            last?.role === 'assistant' &&
            (last.loading?.stage1 || last.loading?.stage2 || last.loading?.stage3);
          if (hasPendingAssistant) {
            const messages = [...prev.messages];
            messages[messages.length - 1] = { ...messages[messages.length - 1], ...assistantMsg };
            return { ...prev, messages };
          }
          return { ...prev, messages: [...prev.messages, assistantMsg] };
        });
      }

      if (activeRun.status === 'running' || activeRun.status === 'queued') {
        const reconnectRequestId = ++requestIdRef.current;
        abortControllerRef.current = new AbortController();
        api.streamRun(
          activeRun.run_id,
          handleRunEvent(id, activeRun.run_id),
          abortControllerRef.current.signal,
          activeRun.event_count || 0
        )
          .catch((error) => {
            if (error.name !== 'AbortError') {
              console.error('Failed to reconnect run stream:', error);
            }
          })
          .finally(() => {
            if (requestIdRef.current === reconnectRequestId) {
              abortControllerRef.current = null;
              setIsLoading(false);
            }
          });
      }
    } catch (error) {
      console.error('Failed to load conversation:', error);
      setIsLoading(false);
    }
  };

  const handleNewConversation = async () => {
    // Check if there's already an empty/unused conversation
    const existingEmpty = conversations.find(conv => !conv.title && conv.message_count === 0);

    if (existingEmpty) {
      // Reuse the existing empty conversation instead of creating a new one
      setCurrentConversationId(existingEmpty.id);
      return;
    }

    try {
      const newConv = await api.createConversation();
      setConversations([
        { id: newConv.id, created_at: newConv.created_at, message_count: 0 },
        ...conversations,
      ]);
      setCurrentConversationId(newConv.id);
    } catch (error) {
      console.error('Failed to create conversation:', error);
    }
  };

  const handleSelectConversation = (id) => {
    setCurrentConversationId(id);
  };

  const handleDeleteConversation = async (id) => {
    try {
      await api.deleteConversation(id);
      // Remove from local state
      setConversations(conversations.filter(c => c.id !== id));
      // If we deleted the current conversation, clear it
      if (id === currentConversationId) {
        setCurrentConversationId(null);
        setCurrentConversation(null);
        sessionStorage.removeItem(CURRENT_CONVERSATION_STORAGE_KEY);
      }
    } catch (error) {
      console.error('Failed to delete conversation:', error);
    }
  };

  const handleAbort = () => {
    const runId = currentConversationId ? activeRuns[currentConversationId] : null;
    if (runId) {
      api.cancelRun(runId).catch((error) => {
        console.error('Failed to cancel run:', error);
      });
    }
    if (abortControllerRef.current) {
      abortControllerRef.current.abort();
    }
    setIsLoading(false);
  };

  const handleRunEvent = (conversationId, runId) => (eventType, event) => {
    switch (eventType) {
      case 'search_start':
        setCurrentConversation((prev) => {
          if (!prev || prev.id !== conversationId) return prev;
          const messages = [...prev.messages];
          const lastMsg = messages[messages.length - 1];
          messages[messages.length - 1] = {
            ...lastMsg,
            loading: { ...lastMsg.loading, search: true }
          };
          return { ...prev, messages };
        });
        break;

      case 'search_complete':
        setCurrentConversation((prev) => {
          if (!prev || prev.id !== conversationId) return prev;
          const messages = [...prev.messages];
          const lastMsg = messages[messages.length - 1];
          messages[messages.length - 1] = {
            ...lastMsg,
            loading: { ...lastMsg.loading, search: false },
            metadata: {
              ...lastMsg.metadata,
              search_query: event.data.search_query,
              extracted_query: event.data.extracted_query,
              search_context: event.data.search_context,
            }
          };
          return { ...prev, messages };
        });
        break;

      case 'stage1_start':
        setCurrentConversation((prev) => {
          if (!prev || prev.id !== conversationId) return prev;
          const messages = [...prev.messages];
          const lastMsg = messages[messages.length - 1];
          messages[messages.length - 1] = {
            ...lastMsg,
            loading: { ...lastMsg.loading, stage1: true },
            timers: { ...lastMsg.timers, stage1Start: Date.now() }
          };
          return { ...prev, messages };
        });
        break;

      case 'stage1_init':
        setCurrentConversation((prev) => {
          if (!prev || prev.id !== conversationId) return prev;
          const messages = [...prev.messages];
          const lastMsg = messages[messages.length - 1];
          messages[messages.length - 1] = {
            ...lastMsg,
            progress: {
              ...lastMsg.progress,
              stage1: { count: 0, total: event.total, currentModel: null }
            }
          };
          return { ...prev, messages };
        });
        break;

      case 'stage1_progress':
        setCurrentConversation((prev) => {
          if (!prev || prev.id !== conversationId) return prev;
          const messages = [...prev.messages];
          const lastMsg = messages[messages.length - 1];
          const updatedStage1 = lastMsg.stage1 ? [...lastMsg.stage1, event.data] : [event.data];
          messages[messages.length - 1] = {
            ...lastMsg,
            progress: {
              ...lastMsg.progress,
              stage1: {
                count: event.count,
                total: event.total,
                currentModel: event.data.model
              }
            },
            stage1: updatedStage1
          };
          return { ...prev, messages };
        });
        break;

      case 'stage1_complete':
        setCurrentConversation((prev) => {
          if (!prev || prev.id !== conversationId) return prev;
          const messages = [...prev.messages];
          const lastMsg = messages[messages.length - 1];
          messages[messages.length - 1] = {
            ...lastMsg,
            stage1: event.data,
            loading: { ...lastMsg.loading, stage1: false },
            timers: { ...lastMsg.timers, stage1End: Date.now() }
          };
          return { ...prev, messages };
        });
        break;

      case 'stage2_start':
        setCurrentConversation((prev) => {
          if (!prev || prev.id !== conversationId) return prev;
          const messages = [...prev.messages];
          const lastMsg = messages[messages.length - 1];
          messages[messages.length - 1] = {
            ...lastMsg,
            loading: { ...lastMsg.loading, stage2: true },
            timers: { ...lastMsg.timers, stage2Start: Date.now() }
          };
          return { ...prev, messages };
        });
        break;

      case 'stage2_init':
        setCurrentConversation((prev) => {
          if (!prev || prev.id !== conversationId) return prev;
          const messages = [...prev.messages];
          const lastMsg = messages[messages.length - 1];
          messages[messages.length - 1] = {
            ...lastMsg,
            progress: {
              ...lastMsg.progress,
              stage2: { count: 0, total: event.total, currentModel: null }
            }
          };
          return { ...prev, messages };
        });
        break;

      case 'stage2_progress':
        setCurrentConversation((prev) => {
          if (!prev || prev.id !== conversationId) return prev;
          const messages = [...prev.messages];
          const lastMsg = messages[messages.length - 1];
          const updatedStage2 = lastMsg.stage2 ? [...lastMsg.stage2, event.data] : [event.data];
          messages[messages.length - 1] = {
            ...lastMsg,
            progress: {
              ...lastMsg.progress,
              stage2: {
                count: event.count,
                total: event.total,
                currentModel: event.data.model
              }
            },
            stage2: updatedStage2
          };
          return { ...prev, messages };
        });
        break;

      case 'stage2_complete':
        setCurrentConversation((prev) => {
          if (!prev || prev.id !== conversationId) return prev;
          const messages = [...prev.messages];
          const lastMsg = messages[messages.length - 1];
          messages[messages.length - 1] = {
            ...lastMsg,
            stage2: event.data,
            loading: { ...lastMsg.loading, stage2: false },
            timers: { ...lastMsg.timers, stage2End: Date.now() },
            metadata: { ...lastMsg.metadata, ...event.metadata }
          };
          return { ...prev, messages };
        });
        break;

      case 'stage3_start':
        setCurrentConversation((prev) => {
          if (!prev || prev.id !== conversationId) return prev;
          const messages = [...prev.messages];
          const lastMsg = messages[messages.length - 1];
          messages[messages.length - 1] = {
            ...lastMsg,
            loading: { ...lastMsg.loading, stage3: true },
            timers: { ...lastMsg.timers, stage3Start: Date.now() }
          };
          return { ...prev, messages };
        });
        break;

      case 'stage3_complete':
        setCurrentConversation((prev) => {
          if (!prev || prev.id !== conversationId) return prev;
          const messages = [...prev.messages];
          const lastMsg = messages[messages.length - 1];
          messages[messages.length - 1] = {
            ...lastMsg,
            stage3: event.data,
            loading: { ...lastMsg.loading, stage3: false },
            timers: { ...lastMsg.timers, stage3End: Date.now() }
          };
          return { ...prev, messages };
        });
        setIsLoading(false);
        break;

      case 'title_complete':
        loadConversations();
        break;

      case 'cancelled':
        setCurrentConversation((prev) => {
          if (!prev || prev.id !== conversationId) return prev;
          const messages = [...prev.messages];
          const lastMsg = messages[messages.length - 1];
          if (lastMsg?.role === 'assistant') {
            messages[messages.length - 1] = {
              ...lastMsg,
              aborted: true,
              loading: { search: false, stage1: false, stage2: false, stage3: false }
            };
          }
          return { ...prev, messages };
        });
        setActiveRuns((prev) => {
          if (prev[conversationId] !== runId) return prev;
          const next = { ...prev };
          delete next[conversationId];
          return next;
        });
        setIsLoading(false);
        loadConversation(conversationId);
        break;

      case 'complete':
        setActiveRuns((prev) => {
          if (prev[conversationId] !== runId) return prev;
          const next = { ...prev };
          delete next[conversationId];
          return next;
        });
        loadConversations();
        setIsLoading(false);
        break;

      case 'error':
        console.error('Run stream error:', event.message);
        setActiveRuns((prev) => {
          if (prev[conversationId] !== runId) return prev;
          const next = { ...prev };
          delete next[conversationId];
          return next;
        });
        setIsLoading(false);
        break;

      default:
        break;
    }
  };

  const handleSendMessage = async (content, webSearch) => {
    if (!currentConversationId) return;

    const targetConversationId = currentConversationId;
    const currentRequestId = ++requestIdRef.current;
    abortControllerRef.current = new AbortController();
    setIsLoading(true);

    try {
      const userMessage = { role: 'user', content };
      const assistantMessage = {
        role: 'assistant',
        stage1: null,
        stage2: null,
        stage3: null,
        metadata: null,
        loading: { search: false, stage1: false, stage2: false, stage3: false },
        timers: {
          stage1Start: null,
          stage1End: null,
          stage2Start: null,
          stage2End: null,
          stage3Start: null,
          stage3End: null,
        },
        progress: {
          stage1: { count: 0, total: 0, currentModel: null },
          stage2: { count: 0, total: 0, currentModel: null }
        }
      };

      setCurrentConversation((prev) => ({
        ...prev,
        messages: [...prev.messages, userMessage, assistantMessage],
      }));

      const run = await api.startRun(targetConversationId, { content, webSearch, executionMode });
      setActiveRuns((prev) => ({ ...prev, [targetConversationId]: run.run_id }));
      await api.streamRun(
        run.run_id,
        handleRunEvent(targetConversationId, run.run_id),
        abortControllerRef.current?.signal
      );
    } catch (error) {
      if (error.name === 'AbortError') {
        return;
      }
      console.error('Failed to send message:', error);
      setCurrentConversation((prev) => ({
        ...prev,
        messages: prev?.messages ? prev.messages.slice(0, -2) : [],
      }));
      setIsLoading(false);
    } finally {
      if (requestIdRef.current === currentRequestId) {
        abortControllerRef.current = null;
      }
      loadConversations();
    }
  };

  // Mobile sidebar handlers
  const handleMobileSelectConversation = (id) => {
    handleSelectConversation(id);
    setSidebarOpen(false); // Close sidebar on mobile after selection
  };

  const handleMobileNewConversation = async () => {
    await handleNewConversation();
    setSidebarOpen(false); // Close sidebar on mobile after creating new conversation
  };

  const handleMobileOpenSettings = () => {
    setShowSettings(true);
    setSidebarOpen(false); // Close sidebar on mobile
  };

  return (
    <div className="app">
      {/* Mobile hamburger menu button */}
      <button 
        className="mobile-menu-btn" 
        onClick={() => setSidebarOpen(true)}
        aria-label="Open menu"
      >
        <span className="hamburger-icon"></span>
      </button>

      <Sidebar
        conversations={conversations}
        currentConversationId={currentConversationId}
        onSelectConversation={handleMobileSelectConversation}
        onNewConversation={handleMobileNewConversation}
        onDeleteConversation={handleDeleteConversation}
        onOpenSettings={handleMobileOpenSettings}
        isLoading={isLoading}
        onAbort={handleAbort}
        isOpen={sidebarOpen}
        onClose={() => setSidebarOpen(false)}
      />
      <ChatInterface
        conversation={currentConversation}
        onSendMessage={handleSendMessage}
        onAbort={handleAbort}
        isLoading={isLoading}
        councilConfigured={councilConfigured}
        councilModels={councilModels}
        chairmanModel={chairmanModel}
        searchProvider={searchProvider}
        onOpenSettings={handleOpenSettings}
        executionMode={executionMode}
        onExecutionModeChange={setExecutionMode}
      />
      {showSettings && (
        <Settings
          onClose={handleSettingsClose}
          ollamaStatus={ollamaStatus}
          onRefreshOllama={testOllamaConnection}
          initialSection={settingsInitialSection}
        />
      )}
    </div>
  );
}

export default App;