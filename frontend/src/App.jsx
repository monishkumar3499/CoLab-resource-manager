import React, { useState, useEffect } from 'react';
import { 
  LayoutDashboard, 
  Users, 
  FolderGit2, 
  Sliders, 
  Play, 
  ArrowRight, 
  AlertTriangle, 
  TrendingUp, 
  TrendingDown,
  CheckCircle2, 
  DollarSign, 
  X, 
  ChevronRight, 
  ChevronLeft,
  ChevronDown,
  Info, 
  RefreshCw, 
  SlidersHorizontal,
  Search,
  MapPin,
  Clock,
  Check,
  Zap,
  Menu,
  Activity,
  Layers,
  ArrowUpRight,
  ArrowDown,
  ShieldCheck, 
  Target,
  Bell,
  User,
  Briefcase,
  BarChart2,
  PanelLeftClose,
  PanelLeftOpen
} from 'lucide-react';

const API_BASE = 'http://127.0.0.1:8000';

function App() {
  const [activeTab, setActiveTab] = useState('dashboard');
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const [overview, setOverview] = useState({ people_count: 0, projects_count: 0, pipeline_count: 0, roles_count: 0 });
  const [pipelineProjects, setPipelineProjects] = useState([]);
  const [people, setPeople] = useState([]);
  const [projects, setProjects] = useState([]);
  const [config, setConfig] = useState(null);
  
  // Custom weights for interactive recommendations
  const [customWeights, setCustomWeights] = useState({});
  const [hasModifiedWeights, setHasModifiedWeights] = useState(false);

  // Selected project for running recommendations
  const [selectedProject, setSelectedProject] = useState(null);
  const [recommendResult, setRecommendResult] = useState(null);
  const [loadingRecommend, setLoadingRecommend] = useState(false);
  const [recommendError, setRecommendError] = useState(null);
  const [modalTab, setModalTab] = useState('staffing');
  const [loadingGlobalAlloc, setLoadingGlobalAlloc] = useState(false);
  const [loadingExcelGen, setLoadingExcelGen] = useState(false);
  const [globalStatus, setGlobalStatus] = useState(null); // { type: 'success'|'error', message: '' }
  const [expandedProjectId, setExpandedProjectId] = useState(null);

  // Search & Filter state
  const [peopleSearch, setPeopleSearch] = useState('');
  const [peopleCOE, setPeopleCOE] = useState('All');
  const [projectSearch, setProjectSearch] = useState('');
  const [projectHealth, setProjectHealth] = useState('All');

  // Load initial data
  useEffect(() => {
    fetchOverview();
    fetchPipelineProjects();
    fetchPeople();
    fetchProjects();
    fetchConfig();
  }, []);

  const fetchOverview = async () => {
    try {
      const res = await fetch(`${API_BASE}/api/overview`);
      const data = await res.json();
      setOverview(data);
    } catch (e) { console.error("Error fetching overview", e); }
  };

  const fetchPipelineProjects = async () => {
    try {
      const res = await fetch(`${API_BASE}/api/pipeline-projects`);
      const data = await res.json();
      setPipelineProjects(data);
    } catch (e) { console.error("Error fetching pipeline projects", e); }
  };

  const fetchPeople = async () => {
    try {
      const res = await fetch(`${API_BASE}/api/people`);
      const data = await res.json();
      setPeople(data);
    } catch (e) { console.error("Error fetching people", e); }
  };

  const fetchProjects = async () => {
    try {
      const res = await fetch(`${API_BASE}/api/projects`);
      const data = await res.json();
      setProjects(data);
    } catch (e) { console.error("Error fetching projects", e); }
  };

  const fetchConfig = async () => {
    try {
      const res = await fetch(`${API_BASE}/api/config`);
      const data = await res.json();
      setConfig(data);
      if (data && data.weights) {
        setCustomWeights(data.weights);
      }
    } catch (e) { console.error("Error fetching config", e); }
  };

  const runRecommendation = async (projectId, customWeightsPayload = null) => {
    setLoadingRecommend(true);
    setRecommendError(null);
    setModalTab('staffing');
    
    // Find the project object
    const proj = pipelineProjects.find(p => p.pipeline_id === projectId);
    setSelectedProject(proj);

    try {
      const body = customWeightsPayload ? { weights: customWeightsPayload } : {};
      const res = await fetch(`${API_BASE}/api/recommend/${projectId}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body)
      });
      if (!res.ok) {
        const errData = await res.json();
        throw new Error(errData.detail || "Failed to run recommendation engine.");
      }
      const data = await res.json();
      setRecommendResult(data);
    } catch (e) {
      console.error(e);
      setRecommendError(e.message);
    } finally {
      setLoadingRecommend(false);
    }
  };

  const handleAllocateStaffing = async (projectId) => {
    setLoadingRecommend(true);
    setRecommendError(null);
    try {
      const body = hasModifiedWeights ? { weights: customWeights } : {};
      const res = await fetch(`${API_BASE}/api/allocate/${projectId}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body)
      });
      if (!res.ok) {
        const errData = await res.json();
        throw new Error(errData.detail || "Failed to commit allocations.");
      }
      const data = await res.json();
      setRecommendResult(data);
      await fetchPipelineProjects();
      await fetchPeople();
      await fetchOverview();
    } catch (e) {
      console.error(e);
      setRecommendError(e.message);
    } finally {
      setLoadingRecommend(false);
    }
  };

  const handleUndoAllocation = async (projectId) => {
    setLoadingRecommend(true);
    setRecommendError(null);
    try {
      const res = await fetch(`${API_BASE}/api/undo-allocate/${projectId}`, {
        method: 'POST'
      });
      if (!res.ok) {
        const errData = await res.json();
        throw new Error(errData.detail || "Failed to revert allocations.");
      }
      await runRecommendation(projectId, hasModifiedWeights ? customWeights : null);
      await fetchPipelineProjects();
      await fetchPeople();
      await fetchOverview();
    } catch (e) {
      console.error(e);
      setRecommendError(e.message);
    } finally {
      setLoadingRecommend(false);
    }
  };

  const handleRunAllAllocations = async () => {
    setLoadingGlobalAlloc(true);
    setGlobalStatus(null);
    try {
      const res = await fetch(`${API_BASE}/api/run-all`, { method: 'POST' });
      if (!res.ok) {
        const errData = await res.json();
        throw new Error(errData.detail || "Failed to execute global allocations.");
      }
      const data = await res.json();
      setGlobalStatus({ type: 'success', message: data.message });
      await fetchPipelineProjects();
      await fetchPeople();
      await fetchOverview();
    } catch (e) {
      console.error(e);
      setGlobalStatus({ type: 'error', message: e.message });
    } finally {
      setLoadingGlobalAlloc(false);
    }
  };

  const handleGenerateExcel = async () => {
    setLoadingExcelGen(true);
    setGlobalStatus(null);
    try {
      const res = await fetch(`${API_BASE}/api/generate-excel`, { method: 'POST' });
      if (!res.ok) {
        const errData = await res.json();
        throw new Error(errData.detail || "Failed to generate Excel workbook.");
      }
      const data = await res.json();
      setGlobalStatus({ type: 'success', message: data.message });
    } catch (e) {
      console.error(e);
      setGlobalStatus({ type: 'error', message: e.message });
    } finally {
      setLoadingExcelGen(false);
    }
  };

  const handleDownloadExcel = () => {
    window.open(`${API_BASE}/api/download-excel`, '_blank');
  };


  const handleWeightChange = (key, val) => {
    setCustomWeights(prev => {
      const updated = { ...prev, [key]: parseFloat(val) };
      setHasModifiedWeights(true);
      return updated;
    });
  };

  const resetWeights = () => {
    if (config && config.weights) {
      setCustomWeights(config.weights);
      setHasModifiedWeights(false);
      if (selectedProject) {
        runRecommendation(selectedProject.pipeline_id, null);
      }
    }
  };

  const triggerCustomRecalculation = () => {
    if (selectedProject) {
      runRecommendation(selectedProject.pipeline_id, customWeights);
    }
  };

  // Filter lists
  const filteredPeople = people.filter(p => {
    const matchesSearch = p.employee_id.toLowerCase().includes(peopleSearch.toLowerCase()) ||
                          p.location.toLowerCase().includes(peopleSearch.toLowerCase()) ||
                          (p.primary_coe && p.primary_coe.toLowerCase().includes(peopleSearch.toLowerCase())) ||
                          (p.skill_text && p.skill_text.toLowerCase().includes(peopleSearch.toLowerCase()));
    const matchesCOE = peopleCOE === 'All' || p.primary_coe === peopleCOE;
    return matchesSearch && matchesCOE;
  });

  const filteredProjects = projects.filter(p => {
    const matchesSearch = p.project_id.toLowerCase().includes(projectSearch.toLowerCase()) ||
                          p.client.toLowerCase().includes(projectSearch.toLowerCase()) ||
                          (p.allocated_employees && p.allocated_employees.some(e => e.toLowerCase().includes(projectSearch.toLowerCase())));
    
    let healthColor = 'Green';
    if (p.health_score < 0.45) healthColor = 'Red';
    else if (p.health_score < 0.60) healthColor = 'Amber';
    
    const matchesHealth = projectHealth === 'All' || healthColor === projectHealth;
    return matchesSearch && matchesHealth;
  });

  const coes = ['All', ...new Set(people.map(p => p.primary_coe).filter(Boolean))];
  const isAllocated = selectedProject ? pipelineProjects.find(p => p.pipeline_id === selectedProject.pipeline_id)?.allocated : false;

  return (
    <div className="dashboard-container">
      
      {/* MOBILE TOP BAR */}
      <div className="mobile-top-bar">
        <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
          <div style={{
            background: 'var(--accent-primary)',
            width: '28px', height: '28px', borderRadius: '6px', display: 'flex', alignItems: 'center', justifyContent: 'center', fontWeight: 'bold', fontSize: '0.8rem'
          }}>
            CL
          </div>
          <span style={{ fontWeight: '600', fontFamily: 'var(--font-display)', fontSize: '0.95rem', letterSpacing: '-0.01em' }}>CoLab Copilot</span>
        </div>
        <button 
          style={{ background: 'transparent', border: 'none', color: '#fff', cursor: 'pointer' }}
          onClick={() => setSidebarOpen(!sidebarOpen)}
        >
          <Menu size={24} />
        </button>
      </div>

      {/* SIDEBAR NAVIGATION - Dark theme */}
      <div className={`sidebar ${sidebarOpen ? 'open' : ''} ${sidebarCollapsed ? 'collapsed' : ''}`}>
        <div className="flex-between" style={{ marginBottom: '32px', padding: '0 8px' }}>
          <div className="flex-gap-12" onClick={() => setSidebarCollapsed(!sidebarCollapsed)} style={{ cursor: 'pointer' }} title={sidebarCollapsed ? "Expand sidebar" : "Collapse sidebar"}>
            <div style={{
              background: 'linear-gradient(135deg, var(--accent-primary) 0%, var(--accent-secondary) 100%)',
              width: '32px', height: '32px', borderRadius: '6px', display: 'flex', alignItems: 'center', justifyContent: 'center', fontWeight: 'bold', color: '#fff'
            }}>
              CL
            </div>
            <div>
              <h3 className="sidebar-logo-text" style={{ fontSize: '1.05rem', color: '#fff', fontWeight: '700' }}>CoLab</h3>
              <span className="sidebar-logo-sub" style={{ fontSize: '0.7rem', color: 'var(--text-secondary-on-sidebar)', display: 'block' }}>Resource Copilot</span>
            </div>
          </div>
          <button 
            className="mobile-only" 
            style={{ display: 'none', background: 'transparent', border: 'none', color: '#fff', cursor: 'pointer' }}
            onClick={() => setSidebarOpen(false)}
          >
            <X size={18} />
          </button>
        </div>

        <div style={{ display: 'flex', flexDirection: 'column', gap: '8px', flex: 1 }}>
          <button 
            className="btn" 
            style={{ 
              justifyContent: 'flex-start', 
              background: activeTab === 'dashboard' ? 'rgba(255, 255, 255, 0.08)' : 'transparent',
              border: 'none',
              color: activeTab === 'dashboard' ? '#fff' : 'var(--text-secondary-on-sidebar)',
              padding: '10px 14px'
            }}
            onClick={() => { setActiveTab('dashboard'); setSidebarOpen(false); }}
          >
            <LayoutDashboard size={18} />
            <span>Dashboard</span>
          </button>
          
          <button 
            className="btn" 
            style={{ 
              justifyContent: 'flex-start', 
              background: activeTab === 'people' ? 'rgba(255, 255, 255, 0.08)' : 'transparent',
              border: 'none',
              color: activeTab === 'people' ? '#fff' : 'var(--text-secondary-on-sidebar)',
              padding: '10px 14px'
            }}
            onClick={() => { setActiveTab('people'); setSidebarOpen(false); }}
          >
            <Users size={18} />
            <span>Resource Pool</span>
          </button>

          <button 
            className="btn" 
            style={{ 
              justifyContent: 'flex-start', 
              background: activeTab === 'projects' ? 'rgba(255, 255, 255, 0.08)' : 'transparent',
              border: 'none',
              color: activeTab === 'projects' ? '#fff' : 'var(--text-secondary-on-sidebar)',
              padding: '10px 14px'
            }}
            onClick={() => { setActiveTab('projects'); setSidebarOpen(false); }}
          >
            <FolderGit2 size={18} />
            <span>Active Projects</span>
          </button>
        </div>

        <div className="sidebar-footer-text" style={{ padding: '16px 8px', fontSize: '0.75rem', borderTop: '1px solid rgba(255,255,255,0.08)', color: 'var(--text-secondary-on-sidebar)' }}>
          <div className="flex-gap-12" style={{ marginBottom: '6px' }}>
            <span style={{ width: '6px', height: '6px', background: 'var(--color-success)', borderRadius: '50%', display: 'inline-block' }}></span>
            <span>API Online (8000)</span>
          </div>
          <span>CoLab v1.0 — Enterprise</span>
        </div>
      </div>

      {/* MAIN CONTAINER */}
      <div className={`main-content ${sidebarCollapsed ? 'sidebar-collapsed' : ''}`}>

        {/* TOP BAR */}
        <div className="top-bar">
          <div className="top-bar-left">
            <button
              onClick={() => setSidebarCollapsed(!sidebarCollapsed)}
              style={{ background: 'transparent', border: '1px solid var(--border-color)', borderRadius: '6px', width: '32px', height: '32px', display: 'flex', alignItems: 'center', justifyContent: 'center', cursor: 'pointer', color: 'var(--text-secondary)' }}
              title={sidebarCollapsed ? "Expand sidebar" : "Collapse sidebar"}
            >
              {sidebarCollapsed ? <PanelLeftOpen size={16} /> : <PanelLeftClose size={16} />}
            </button>
            <div style={{ display: 'flex', alignItems: 'center', gap: '6px', background: 'var(--bg-tertiary)', border: '1px solid var(--border-color)', borderRadius: '6px', padding: '6px 12px', width: '240px' }}>
              <Search size={14} style={{ color: 'var(--text-muted)' }} />
              <input
                placeholder="Search resources, projects..."
                style={{ background: 'transparent', border: 'none', outline: 'none', fontSize: '0.8rem', color: 'var(--text-primary)', width: '100%' }}
              />
            </div>
          </div>
          <div className="top-bar-right">
            <div style={{ display: 'flex', alignItems: 'center', gap: '6px', background: 'var(--bg-tertiary)', border: '1px solid var(--border-color)', borderRadius: '6px', padding: '4px 10px', fontSize: '0.78rem', color: 'var(--text-secondary)' }}>
              <Clock size={13} />
              <span>Q2 FY2026</span>
              <ChevronDown size={13} />
            </div>
            <button style={{ background: 'transparent', border: 'none', cursor: 'pointer', color: 'var(--text-secondary)', display: 'flex', alignItems: 'center', position: 'relative' }}>
              <Bell size={17} />
            </button>
            <div style={{ display: 'flex', alignItems: 'center', gap: '8px', background: 'var(--bg-tertiary)', border: '1px solid var(--border-color)', borderRadius: '6px', padding: '4px 10px', cursor: 'pointer' }}>
              <div style={{ width: '22px', height: '22px', borderRadius: '50%', background: 'var(--accent-primary)', display: 'flex', alignItems: 'center', justifyContent: 'center', color: '#fff', fontSize: '0.65rem', fontWeight: '700' }}>DM</div>
              <span style={{ fontSize: '0.78rem', fontWeight: '500', color: 'var(--text-primary)' }}>Delivery Manager</span>
              <ChevronDown size={12} style={{ color: 'var(--text-muted)' }} />
            </div>
          </div>
        </div>

        {/* DASHBOARD TAB */}
        {activeTab === 'dashboard' && (
          <div className="animate-fade-in" style={{ padding: '20px' }}>
            {/* Page header - compact */}
              <div className="flex-between" style={{ marginBottom: '16px' }}>
                <div>
                  <h1 style={{ fontSize: '1.35rem', fontWeight: '700', color: 'var(--text-primary)', marginBottom: '2px' }}>
                    Resource Staffing &amp; Planning
                  </h1>
                  <p style={{ color: 'var(--text-secondary)', fontSize: '0.8rem', margin: 0 }}>
                    Allocations · Swap chains · AI justification reports
                  </p>
                </div>
                <div style={{ display: 'flex', gap: '8px', alignItems: 'center' }}>
                  <button 
                    className="btn btn-secondary" 
                    onClick={() => { fetchOverview(); fetchPipelineProjects(); }}
                    style={{ borderRadius: '6px', padding: '6px 12px', fontSize: '0.78rem', gap: '6px' }}
                  >
                    <RefreshCw size={13} /> Refresh
                  </button>
                  <button 
                    className="btn" 
                    onClick={handleRunAllAllocations}
                    disabled={loadingGlobalAlloc}
                    style={{ 
                      borderRadius: '6px', padding: '6px 12px', fontSize: '0.78rem', gap: '6px',
                      background: 'rgba(37, 99, 235, 0.06)', 
                      color: 'var(--accent-primary)',
                      border: '1px solid rgba(37, 99, 235, 0.18)',
                      cursor: loadingGlobalAlloc ? 'not-allowed' : 'pointer'
                    }}
                  >
                    {loadingGlobalAlloc ? <RefreshCw size={13} className="animate-spin" /> : <Play size={13} />}
                    Run Engine
                  </button>
                  <button 
                    className="btn"
                    onClick={handleGenerateExcel}
                    disabled={loadingExcelGen}
                    style={{ 
                      borderRadius: '6px', padding: '6px 12px', fontSize: '0.78rem', gap: '6px',
                      background: 'rgba(14, 165, 233, 0.06)', 
                      color: 'var(--accent-secondary)',
                      border: '1px solid rgba(14, 165, 233, 0.18)',
                      cursor: loadingExcelGen ? 'not-allowed' : 'pointer'
                    }}
                  >
                    {loadingExcelGen ? <RefreshCw size={13} className="animate-spin" /> : <Zap size={13} />}
                    Export
                  </button>
                  <button 
                    className="btn btn-primary" 
                    onClick={handleDownloadExcel}
                    style={{ borderRadius: '6px', padding: '6px 14px', fontSize: '0.78rem', gap: '6px' }}
                  >
                    <TrendingUp size={13} /> Download
                  </button>
                </div>
              </div>

            {globalStatus && (
              <div style={{ 
                marginBottom: '14px', 
                padding: '9px 14px', 
                borderRadius: '6px', 
                background: globalStatus.type === 'success' ? 'rgba(16, 185, 129, 0.07)' : 'rgba(239, 68, 68, 0.07)',
                border: `1px solid ${globalStatus.type === 'success' ? 'rgba(16, 185, 129, 0.18)' : 'rgba(239, 68, 68, 0.18)'}`,
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'space-between',
                gap: '12px'
              }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: '8px', flex: 1 }}>
                  {globalStatus.type === 'success' ? (
                    <CheckCircle2 size={15} style={{ color: 'var(--color-success)' }} />
                  ) : (
                    <AlertTriangle size={15} style={{ color: 'var(--color-danger)' }} />
                  )}
                  <span style={{ fontSize: '0.82rem', color: globalStatus.type === 'success' ? 'var(--color-success)' : 'var(--color-danger)', fontWeight: '500' }}>
                    {globalStatus.message}
                  </span>
                </div>
                <button 
                  onClick={() => setGlobalStatus(null)}
                  style={{ background: 'transparent', border: 'none', cursor: 'pointer', color: 'var(--text-secondary)' }}
                >
                  <X size={13} />
                </button>
              </div>
            )}

            {/* COMPACT KPI CARDS */}
            <div className="stats-grid">
              <div className="stat-card">
                <div className="stat-header">
                  <span className="stat-label">Pipeline Projects</span>
                  <Briefcase size={15} className="stat-icon" />
                </div>
                <div className="stat-body">
                  <span className="stat-value">{overview.pipeline_count}</span>
                  <div className="stat-trend-container">
                    <span className="stat-trend up"><TrendingUp size={11} /> Active</span>
                  </div>
                </div>
              </div>
              <div className="stat-card">
                <div className="stat-header">
                  <span className="stat-label">Open Requirements</span>
                  <Target size={15} className="stat-icon" />
                </div>
                <div className="stat-body">
                  <span className="stat-value">{overview.roles_count}</span>
                  <div className="stat-trend-container">
                    <span className="stat-trend" style={{ color: 'var(--color-warning)' }}><Activity size={11} /> Pending</span>
                  </div>
                </div>
              </div>
              <div className="stat-card">
                <div className="stat-header">
                  <span className="stat-label">Resource Pool</span>
                  <Users size={15} className="stat-icon" />
                </div>
                <div className="stat-body">
                  <span className="stat-value">{overview.people_count}</span>
                  <div className="stat-trend-container">
                    <span className="stat-trend up"><ArrowUpRight size={11} /> Available</span>
                  </div>
                </div>
              </div>
              <div className="stat-card">
                <div className="stat-header">
                  <span className="stat-label">Active Deployments</span>
                  <BarChart2 size={15} className="stat-icon" />
                </div>
                <div className="stat-body">
                  <span className="stat-value">{overview.projects_count}</span>
                  <div className="stat-trend-container">
                    <span className="stat-trend up"><ShieldCheck size={11} /> Live</span>
                  </div>
                </div>
              </div>
            </div>

            {/* PIPELINE REQUESTS TABLE */}
            <div className="glass-panel panel-container">
              <div style={{ marginBottom: '16px' }}>
                <h2 style={{ fontSize: '1.2rem', marginBottom: '4px' }}>Incoming Project Staffing Pipeline</h2>
                <p style={{ color: 'var(--text-secondary)', fontSize: '0.85rem' }}>Select a requirement below to calculate allocations, simulated swap paths, and client impact.</p>
              </div>

              <div className="table-wrapper">
                <table className="custom-table compact-table">
                  <thead>
                    <tr>
                      <th style={{ width: '25%' }}>Project Requirement ID</th>
                      <th style={{ width: '12%' }}>Client Name</th>
                      <th style={{ width: '10%' }}>Client Tier</th>
                      <th style={{ width: '10%' }}>Priority</th>
                      <th style={{ width: '10%' }}>Required Roles</th>
                      <th style={{ width: '10%' }}>SOW Status</th>
                      <th style={{ width: '10%' }}>Allocation Status</th>
                      <th style={{ width: '10%' }}>Expected Start</th>
                      <th style={{ textAlign: 'right', width: '3%' }}>Actions</th>
                    </tr>
                  </thead>
                  <tbody>
                    {pipelineProjects.map((proj) => {
                      const priorityBadge = 
                        proj.priority === 'Urgent' ? 'badge-urgent' :
                        proj.priority === 'High' ? 'badge-high' :
                        proj.priority === 'Medium' ? 'badge-medium' : 'badge-low';

                      const sowBadge = proj.sow_signed ? 'badge-success' : 'badge-low';
                      const isExpanded = expandedProjectId === proj.pipeline_id;

                      return (
                        <React.Fragment key={proj.pipeline_id}>
                          <tr 
                            onClick={() => setExpandedProjectId(isExpanded ? null : proj.pipeline_id)}
                            style={{ cursor: 'pointer', background: isExpanded ? 'var(--bg-tertiary)' : '' }}
                          >
                            <td style={{ fontWeight: '600', color: 'var(--text-primary)' }}>{proj.pipeline_id}</td>
                            <td>{proj.client}</td>
                            <td>
                              <span className="badge badge-medium" style={{ background: proj.client_priority === 'Gold' ? 'rgba(250, 204, 21, 0.1)' : '', color: proj.client_priority === 'Gold' ? '#eab308' : '', borderColor: proj.client_priority === 'Gold' ? 'rgba(250, 204, 21, 0.2)' : '' }}>
                                {proj.client_priority || 'Other'}
                              </span>
                            </td>
                            <td>
                              <span className={`badge ${priorityBadge}`}>{proj.priority}</span>
                            </td>
                            <td>
                              <span style={{ fontWeight: '500' }}>{proj.role_count || 1} slots</span>
                            </td>
                            <td>
                              <span className={`badge ${sowBadge}`}>
                                {proj.sow_signed ? 'Signed' : 'Pending'}
                              </span>
                            </td>
                            <td>
                              <span className={`badge ${proj.allocated ? 'badge-success' : 'badge-low'}`}>
                                {proj.allocated ? 'Allocated' : 'Draft'}
                              </span>
                            </td>
                            <td>{proj.likely_start_str || 'TBD'}</td>
                            <td style={{ textAlign: 'right', color: 'var(--accent-primary)', fontWeight: '600', fontSize: '0.75rem' }}>
                              {isExpanded ? 'Collapse' : 'Expand'}
                            </td>
                          </tr>
                          {isExpanded && (
                            <tr>
                              <td colSpan={9} style={{ background: 'var(--bg-primary)', padding: '12px 18px', borderBottom: '1px solid var(--border-color)' }}>
                                <div style={{ 
                                  display: 'flex', 
                                  justifyContent: 'space-between', 
                                  alignItems: 'center',
                                  background: 'var(--bg-secondary)',
                                  border: '1px solid var(--border-color)',
                                  borderRadius: '8px',
                                  padding: '12px 16px',
                                  boxShadow: 'var(--shadow-sm)'
                                }}>
                                  <div>
                                    <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '4px' }}>
                                      <span style={{ fontSize: '0.85rem', fontWeight: '600', color: 'var(--text-primary)' }}>CoLab Staffing Recommender</span>
                                      <span className={`badge ${proj.allocated ? 'badge-success' : 'badge-low'}`} style={{ fontSize: '0.65rem' }}>
                                        {proj.allocated ? 'Staffed' : 'Unallocated'}
                                      </span>
                                    </div>
                                    <p style={{ fontSize: '0.75rem', color: 'var(--text-secondary)', margin: 0 }}>
                                      Project requires <strong>{proj.role_count || 1} skill slots</strong> starting <strong>{proj.likely_start_str || 'TBD'}</strong>. Urgency: <strong>{proj.priority}</strong>.
                                    </p>
                                  </div>
                                  <button 
                                    className="btn btn-primary" 
                                    style={{ padding: '6px 12px', fontSize: '0.8rem', borderRadius: '6px', height: '32px' }}
                                    onClick={(e) => {
                                      e.stopPropagation();
                                      runRecommendation(proj.pipeline_id);
                                    }}
                                  >
                                    <Play size={10} fill="white" style={{ marginRight: '6px' }} /> Recommend Resource
                                  </button>
                                </div>
                              </td>
                            </tr>
                          )}
                        </React.Fragment>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            </div>
          </div>
        )}

        {/* RESOURCE POOL TAB */}
        {activeTab === 'people' && (
          <div className="animate-fade-in" style={{ padding: '20px' }}>
            <div style={{ marginBottom: '14px' }}>
              <h1 style={{ fontSize: '1.35rem', fontWeight: '700', color: 'var(--text-primary)', marginBottom: '2px' }}>Resource Pool</h1>
              <p style={{ color: 'var(--text-secondary)', fontSize: '0.8rem', margin: 0 }}>View skill data, geographic locations, and current capacities across delivery teams.</p>
            </div>

            <div className="glass-panel panel-container" style={{ marginBottom: '16px' }}>
              <div style={{ display: 'flex', gap: '16px', flexWrap: 'wrap' }}>
                <div style={{ flex: 1, minWidth: '260px', position: 'relative' }}>
                  <input 
                    type="text" 
                    placeholder="Search resources by ID, location, skills, or COE..." 
                    value={peopleSearch}
                    onChange={(e) => setPeopleSearch(e.target.value)}
                    style={{
                      width: '100%', padding: '10px 16px 10px 40px', background: 'var(--bg-secondary)',
                      border: '1px solid var(--border-color)', borderRadius: '6px', color: 'var(--text-primary)', fontSize: '0.875rem', outline: 'none'
                    }}
                  />
                  <Search size={16} style={{ position: 'absolute', left: '14px', top: '13px', color: 'var(--text-muted)' }} />
                </div>
                <div style={{ width: '200px' }}>
                  <select 
                    value={peopleCOE} 
                    onChange={(e) => setPeopleCOE(e.target.value)}
                    style={{
                      width: '100%', padding: '10px 16px', background: 'var(--bg-secondary)',
                      border: '1px solid var(--border-color)', borderRadius: '6px', color: 'var(--text-primary)', fontSize: '0.875rem', outline: 'none', cursor: 'pointer'
                    }}
                  >
                    {coes.map(coe => (
                      <option key={coe} value={coe}>{coe === 'All' ? 'All COEs' : coe}</option>
                    ))}
                  </select>
                </div>
              </div>
            </div>

            <div className="table-wrapper">
              <table className="custom-table compact-table">
                <thead>
                  <tr>
                    <th style={{ width: '15%' }}>Employee ID</th>
                    <th style={{ width: '25%' }}>COE Domain</th>
                    <th style={{ width: '20%' }}>Primary Location</th>
                    <th style={{ width: '15%' }}>Seniority Tier</th>
                    <th style={{ width: '15%' }}>Available Capacity</th>
                    <th style={{ width: '10%' }}>Current Active Projects</th>
                  </tr>
                </thead>
                <tbody>
                  {filteredPeople.map((p) => {
                    const capacityColor = 
                      p.effective_availability > 60 ? 'var(--color-success)' :
                      p.effective_availability > 30 ? 'var(--color-warning)' : 'var(--color-danger)';

                    return (
                      <tr key={p.employee_id}>
                        <td style={{ fontWeight: '600', color: 'var(--text-primary)' }}>{p.employee_id}</td>
                        <td>{p.primary_coe}</td>
                        <td>
                          <span style={{ display: 'inline-flex', alignItems: 'center', gap: '6px' }}>
                            <MapPin size={12} style={{ color: 'var(--text-secondary)' }} /> {p.location}
                          </span>
                        </td>
                        <td>
                          <span style={{ fontWeight: '500' }}>Tier {p.seniority_tier}</span>
                        </td>
                        <td>
                          <strong style={{ color: capacityColor }}>
                            {p.effective_availability.toFixed(0)}% Available
                          </strong>
                        </td>
                        <td>
                          {p.active_project_ids && p.active_project_ids.length > 0 ? (
                            <div style={{ display: 'flex', flexWrap: 'wrap', gap: '4px' }}>
                              {p.active_project_ids.map(pid => (
                                <span key={pid} style={{ fontSize: '0.7rem', padding: '2px 6px', background: 'var(--bg-tertiary)', border: '1px solid var(--border-color)', borderRadius: '4px', color: 'var(--text-secondary)' }}>
                                  {pid}
                                </span>
                              ))}
                            </div>
                          ) : (
                            <span style={{ color: 'var(--text-muted)', fontSize: '0.8rem' }}>On Bench</span>
                          )}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </div>
        )}

        {/* ACTIVE PROJECTS TAB */}
        {activeTab === 'projects' && (
          <div className="animate-fade-in" style={{ padding: '20px' }}>
            <div style={{ marginBottom: '14px' }}>
              <h1 style={{ fontSize: '1.35rem', fontWeight: '700', color: 'var(--text-primary)', marginBottom: '2px' }}>Active Projects</h1>
              <p style={{ color: 'var(--text-secondary)', fontSize: '0.8rem', margin: 0 }}>Audit delivery health, CSAT rankings, and current project team allocations.</p>
            </div>

            <div className="glass-panel panel-container" style={{ marginBottom: '16px' }}>
              <div style={{ display: 'flex', gap: '16px', flexWrap: 'wrap' }}>
                <div style={{ flex: 1, minWidth: '260px', position: 'relative' }}>
                  <input 
                    type="text" 
                    placeholder="Search projects by ID, client, or team..." 
                    value={projectSearch}
                    onChange={(e) => setProjectSearch(e.target.value)}
                    style={{
                      width: '100%', padding: '10px 16px 10px 40px', background: 'var(--bg-secondary)',
                      border: '1px solid var(--border-color)', borderRadius: '6px', color: 'var(--text-primary)', fontSize: '0.875rem', outline: 'none'
                    }}
                  />
                  <Search size={16} style={{ position: 'absolute', left: '14px', top: '13px', color: 'var(--text-muted)' }} />
                </div>
                <div style={{ width: '200px' }}>
                  <select 
                    value={projectHealth} 
                    onChange={(e) => setProjectHealth(e.target.value)}
                    style={{
                      width: '100%', padding: '10px 16px', background: 'var(--bg-secondary)',
                      border: '1px solid var(--border-color)', borderRadius: '6px', color: 'var(--text-primary)', fontSize: '0.875rem', outline: 'none', cursor: 'pointer'
                    }}
                  >
                    <option value="All">All Health Statuses</option>
                    <option value="Green">Green Health (&ge; 0.60)</option>
                    <option value="Amber">Amber Health (0.45 - 0.59)</option>
                    <option value="Red">Red Health (&lt; 0.45)</option>
                  </select>
                </div>
              </div>
            </div>

            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(300px, 1fr))', gap: '20px' }}>
              {filteredProjects.map((p) => {
                let healthClass = 'badge-success';
                let cardBorderColor = 'var(--border-color)';
                let glowColor = 'rgba(0,0,0,0.03)';
                if (p.health_score < 0.45) {
                  healthClass = 'badge-urgent';
                  cardBorderColor = 'rgba(239, 68, 68, 0.2)';
                  glowColor = 'rgba(239, 68, 68, 0.02)';
                } else if (p.health_score < 0.60) {
                  healthClass = 'badge-high';
                  cardBorderColor = 'rgba(245, 158, 11, 0.2)';
                  glowColor = 'rgba(245, 158, 11, 0.02)';
                }

                return (
                  <div key={p.project_id} className="glass-panel" style={{ padding: '20px', borderColor: cardBorderColor, background: `linear-gradient(180deg, #ffffff 0%, ${glowColor} 100%)` }}>
                    <div className="flex-between" style={{ marginBottom: '14px' }}>
                      <div>
                        <h4 style={{ fontSize: '0.95rem', fontWeight: '700' }}>{p.project_id}</h4>
                        <span style={{ fontSize: '0.75rem', color: 'var(--text-secondary)' }}>Client: {p.client}</span>
                      </div>
                      <span className={`badge ${healthClass}`}>
                        Health: {p.health_score.toFixed(2)}
                      </span>
                    </div>

                    <div style={{ display: 'flex', flexDirection: 'column', gap: '8px', fontSize: '0.85rem', marginBottom: '14px', borderBottom: '1px solid var(--border-color)', paddingBottom: '12px' }}>
                      <div className="flex-between">
                        <span style={{ color: 'var(--text-secondary)' }}>Extension Risk:</span>
                        <span style={{ fontWeight: '600', color: p.extension_risk > 0.5 ? 'var(--color-danger)' : 'var(--text-primary)' }}>
                          {(p.extension_risk * 100).toFixed(0)}%
                        </span>
                      </div>
                      <div className="flex-between">
                        <span style={{ color: 'var(--text-secondary)' }}>Team Size:</span>
                        <span>{p.allocated_employees ? p.allocated_employees.length : 0} / {p.total_slots} allocated</span>
                      </div>
                      <div className="flex-between">
                        <span style={{ color: 'var(--text-secondary)' }}>Latest CSAT:</span>
                        <span>{p.latest_csat === 'NO_COLOR' ? 'N/A' : (p.latest_csat || 'N/A')}</span>
                      </div>
                    </div>

                    <div>
                      <span style={{ fontSize: '0.75rem', color: 'var(--text-secondary)', display: 'block', marginBottom: '6px', fontWeight: '500' }}>Allocated Team Members:</span>
                      <div style={{ display: 'flex', flexWrap: 'wrap', gap: '4px', alignItems: 'center' }}>
                        {p.allocated_employees && p.allocated_employees.length > 0 ? (
                          <>
                            {p.allocated_employees.slice(0, 3).map(emp => (
                              <span key={emp} style={{ fontSize: '0.7rem', padding: '2px 6px', background: 'var(--bg-tertiary)', border: '1px solid var(--border-color)', borderRadius: '4px', color: 'var(--text-primary)' }}>
                                {emp}
                              </span>
                            ))}
                            {p.allocated_employees.length > 3 && (
                              <span 
                                style={{ fontSize: '0.7rem', padding: '2px 6px', background: 'var(--accent-primary-glow)', border: '1px solid rgba(79, 70, 229, 0.2)', borderRadius: '4px', color: 'var(--accent-primary)', fontWeight: '600', cursor: 'help' }}
                                title={p.allocated_employees.slice(3).join(', ')}
                              >
                                + {p.allocated_employees.length - 3} more
                              </span>
                            )}
                          </>
                        ) : (
                          <span style={{ color: 'var(--text-muted)', fontSize: '0.75rem', fontStyle: 'italic' }}>No active staffing</span>
                        )}
                      </div>
                    </div>
                  </div>
                );
              })}
            </div>
          </div>
        )}



      </div>

      {/* FIXED HEIGHT RECOMMENDATION DETAILS MODAL - Sticky header/footer, scrollable body */}
      {selectedProject && (
        <div className="modal-overlay">
          <div className="modal-content">
            
            {/* Modal Header (Fixed) */}
            <div className="modal-header">
              <div className="flex-between">
                <div>
                  <span className="badge badge-urgent" style={{ marginBottom: '6px', fontSize: '0.65rem' }}>
                    Recommendation Plan
                  </span>
                  <h2 style={{ fontSize: '1.35rem', fontFamily: 'var(--font-display)', color: 'var(--text-primary)' }}>
                    {selectedProject.client} — {selectedProject.pipeline_id}
                  </h2>
                  <div style={{ display: 'flex', gap: '16px', marginTop: '4px', fontSize: '0.8rem', color: 'var(--text-secondary)' }}>
                    <span>Urgency: <strong>{selectedProject.priority}</strong></span>
                    <span>Client Tier: <strong>{selectedProject.client_priority || 'Other'}</strong></span>
                    <span>Expected Start: <strong>{selectedProject.likely_start_str || 'TBD'}</strong></span>
                  </div>
                </div>
                <button 
                  className="btn btn-secondary" 
                  style={{ borderRadius: '50%', width: '32px', height: '32px', padding: '0', display: 'flex', alignItems: 'center', justifyContent: 'center' }}
                  onClick={() => { setSelectedProject(null); setRecommendResult(null); }}
                >
                  <X size={16} />
                </button>
              </div>
            </div>

            {/* Loading Indicator */}
            {loadingRecommend && (
              <div style={{ flex: 1, display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', gap: '16px', padding: '40px', background: 'var(--bg-primary)' }}>
                <RefreshCw size={32} className="animate-spin" style={{ color: 'var(--accent-primary)', animation: 'spin 1s linear infinite' }} />
                <h4 style={{ color: 'var(--text-primary)', fontWeight: '600' }}>Running recommendation engine...</h4>
                <p style={{ color: 'var(--text-secondary)', fontSize: '0.8rem', textAlign: 'center', maxWidth: '360px' }}>Analyzing candidate skill matching, availability constraints, and business rules.</p>
              </div>
            )}

            {/* Error Message */}
            {recommendError && (
              <div style={{ flex: 1, padding: '40px', textAlign: 'center', background: 'var(--bg-primary)' }}>
                <AlertTriangle size={40} style={{ color: 'var(--color-danger)', marginBottom: '16px' }} />
                <h3 style={{ color: 'var(--text-primary)', marginBottom: '8px' }}>Calculation Error</h3>
                <p style={{ color: 'var(--text-secondary)', marginBottom: '20px', fontSize: '0.9rem' }}>{recommendError}</p>
                <button className="btn btn-primary" onClick={() => runRecommendation(selectedProject.pipeline_id, hasModifiedWeights ? customWeights : null)}>
                  Retry Run
                </button>
              </div>
            )}

            {/* Modal Body & Interactive Banner */}
            {!loadingRecommend && recommendResult && (
              <>
                {/* Weights Banner (Fixed) */}
                <div className="modal-banner">
                  <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap', gap: '12px' }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                      <SlidersHorizontal size={14} style={{ color: 'var(--accent-primary)' }} />
                      <span style={{ fontSize: '0.8rem', fontWeight: '600', color: 'var(--text-primary)' }}>Tweak parameters:</span>
                    </div>
                    
                    <div style={{ display: 'flex', gap: '12px', flex: 1, overflowX: 'auto', padding: '2px 0' }}>
                      {['semantic_similarity', 'skill_confidence', 'availability', 'location_preference'].map(weightKey => {
                        const formatted = weightKey.split('_')[0].toUpperCase();
                        return (
                          <div key={weightKey} style={{ display: 'flex', alignItems: 'center', gap: '4px', minWidth: '115px' }}>
                            <span style={{ fontSize: '0.7rem', color: 'var(--text-secondary)' }}>{formatted}:</span>
                            <input 
                              type="range" 
                              min="0" 
                              max="0.4" 
                              step="0.02" 
                              value={customWeights[weightKey] || 0}
                              onChange={(e) => handleWeightChange(weightKey, e.target.value)}
                              style={{ width: '45px', height: '3px', accentColor: 'var(--accent-primary)' }}
                            />
                            <span style={{ fontSize: '0.7rem', fontFamily: 'monospace', fontWeight: '600' }}>{(customWeights[weightKey] || 0).toFixed(2)}</span>
                          </div>
                        );
                      })}
                    </div>

                    <div style={{ display: 'flex', gap: '8px' }}>
                      <button className="btn btn-primary" style={{ padding: '4px 10px', fontSize: '0.75rem', borderRadius: '4px' }} onClick={triggerCustomRecalculation}>
                        Re-run
                      </button>
                      {hasModifiedWeights && (
                        <button className="btn btn-secondary" style={{ padding: '4px 10px', fontSize: '0.75rem', borderRadius: '4px' }} onClick={resetWeights}>
                          Reset
                        </button>
                      )}
                    </div>
                  </div>
                </div>

                {/* Modal Tab Buttons (Fixed) */}
                <div className="modal-tabs">
                  <button 
                    className={`modal-tabs-btn ${modalTab === 'staffing' ? 'active' : ''}`}
                    onClick={() => setModalTab('staffing')}
                  >
                    Role Allocations
                  </button>
                  <button 
                    className={`modal-tabs-btn ${modalTab === 'swap' ? 'active' : ''}`}
                    onClick={() => setModalTab('swap')}
                  >
                    Replacement Chains ({recommendResult.project_plan.roles_filled_via_swap || 0})
                  </button>
                  <button 
                    className={`modal-tabs-btn ${modalTab === 'summary' ? 'active' : ''}`}
                    onClick={() => setModalTab('summary')}
                  >
                    AI Justification Summary
                  </button>
                </div>

                {/* SCROLLABLE modal body */}
                <div className="modal-body">
                  
                  {/* TAB 1: ROLE ALLOCATIONS */}
                  {modalTab === 'staffing' && (
                    <div>
                      {/* Overall metrics row */}
                      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: '16px', marginBottom: '24px' }}>
                        <div style={{ background: '#fff', border: '1px solid var(--border-color)', padding: '12px 16px', borderRadius: '8px' }}>
                          <span style={{ fontSize: '0.75rem', color: 'var(--text-secondary)', display: 'block', fontWeight: '500' }}>Coverage Score</span>
                          <strong style={{ fontSize: '1.25rem', color: 'var(--text-primary)' }}>{recommendResult.project_plan.coverage_pct.toFixed(0)}%</strong>
                        </div>
                        <div style={{ background: '#fff', border: '1px solid var(--border-color)', padding: '12px 16px', borderRadius: '8px' }}>
                          <span style={{ fontSize: '0.75rem', color: 'var(--text-secondary)', display: 'block', fontWeight: '500' }}>Immediate (Plan A)</span>
                          <strong style={{ fontSize: '1.25rem', color: 'var(--color-success)' }}>{recommendResult.project_plan.roles_filled_immediate} roles</strong>
                        </div>
                        <div style={{ background: '#fff', border: '1px solid var(--border-color)', padding: '12px 16px', borderRadius: '8px' }}>
                          <span style={{ fontSize: '0.75rem', color: 'var(--text-secondary)', display: 'block', fontWeight: '500' }}>Swaps (Plan B)</span>
                          <strong style={{ fontSize: '1.25rem', color: 'var(--color-warning)' }}>{recommendResult.project_plan.roles_filled_via_swap} roles</strong>
                        </div>
                        <div style={{ background: '#fff', border: '1px solid var(--border-color)', padding: '12px 16px', borderRadius: '8px' }}>
                          <span style={{ fontSize: '0.75rem', color: 'var(--text-secondary)', display: 'block', fontWeight: '500' }}>Hires (Plan D)</span>
                          <strong style={{ fontSize: '1.25rem', color: 'var(--color-danger)' }}>{recommendResult.project_plan.roles_needing_hire} roles</strong>
                        </div>
                      </div>

                      {/* Open role allocations cards list */}
                      <div style={{ display: 'flex', flexDirection: 'column', gap: '16px' }}>
                        {recommendResult.project_plan.role_plans.map((rolePlan) => {
                          const opt = rolePlan.recommended_option;
                          let planBadgeClass = 'badge-success';
                          let planText = 'Immediate (Plan A)';
                          
                          if (opt) {
                            if (opt.plan_type === 'B_SWAP') {
                              planBadgeClass = 'badge-high';
                              planText = 'Smart Swap (Plan B)';
                            } else if (opt.plan_type === 'C_WAIT') {
                              planBadgeClass = 'badge-medium';
                              planText = 'Wait / Committing (Plan C)';
                            } else if (opt.plan_type === 'D_HIRE') {
                              planBadgeClass = 'badge-urgent';
                              planText = 'External Sourcing (Plan D)';
                            }
                          } else {
                            planBadgeClass = 'badge-urgent';
                            planText = 'Unresolved Gap';
                          }

                          return (
                            <div key={rolePlan.role_id} style={{ background: '#fff', border: '1px solid var(--border-color)', padding: '20px', borderRadius: '10px' }}>
                              <div className="flex-between" style={{ marginBottom: '14px' }}>
                                <div>
                                  <h4 style={{ color: 'var(--text-primary)', fontSize: '1rem', fontWeight: '600' }}>
                                    {rolePlan.role_name}
                                  </h4>
                                  <span style={{ fontSize: '0.75rem', color: 'var(--text-secondary)' }}>
                                    Tier {rolePlan.seniority_tier} | required: {rolePlan.required_pct}% allocation
                                  </span>
                                </div>
                                <span className={`badge ${planBadgeClass}`}>{planText}</span>
                              </div>

                              {opt ? (
                                <div style={{ display: 'flex', flexWrap: 'wrap', gap: '20px', fontSize: '0.85rem' }}>
                                  <div style={{ flex: '1', minWidth: '160px' }}>
                                    <span style={{ color: 'var(--text-secondary)', display: 'block', fontSize: '0.75rem' }}>Assigned Resource</span>
                                    <strong style={{ color: 'var(--text-primary)', fontSize: '0.95rem' }}>
                                      {opt.recommended_employee_id || 'EXTERNAL HIRE'}
                                    </strong>
                                    <span style={{ display: 'block', fontSize: '0.75rem', color: 'var(--text-secondary)' }}>
                                      {opt.job_name || 'Hiring Pipeline'} | {opt.location || 'Global pool'}
                                    </span>
                                  </div>
                                  
                                  <div style={{ flex: '1', minWidth: '140px' }}>
                                    <span style={{ color: 'var(--text-secondary)', display: 'block', fontSize: '0.75rem' }}>Evaluation Score</span>
                                    <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
                                      <strong style={{ fontSize: '1rem', color: 'var(--accent-primary)' }}>
                                        {opt.composite_score.toFixed(3)}
                                      </strong>
                                      <span style={{ fontSize: '0.7rem', color: 'var(--text-secondary)' }}>
                                        ({opt.confidence_band})
                                      </span>
                                    </div>
                                    <span style={{ display: 'block', fontSize: '0.75rem', color: 'var(--text-secondary)' }}>
                                      Delay: {opt.estimated_delay_days} days
                                    </span>
                                  </div>

                                  <div style={{ flex: '1.8', minWidth: '220px' }}>
                                    <span style={{ color: 'var(--text-secondary)', display: 'block', fontSize: '0.75rem', marginBottom: '4px' }}>Model Score Weights Analysis:</span>
                                    <div style={{ display: 'flex', flexDirection: 'column', gap: '3px', fontSize: '0.75rem' }}>
                                      {opt.score_breakdown && Object.entries(opt.score_breakdown).slice(0, 4).map(([scoreKey, scoreVal]) => (
                                        <div key={scoreKey} style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                                          <span style={{ width: '80px', color: 'var(--text-secondary)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                                            {scoreKey.replace('_', ' ')}
                                          </span>
                                          <div style={{ flex: 1, height: '4px', background: 'var(--bg-tertiary)', borderRadius: '2px', overflow: 'hidden' }}>
                                            <div style={{ width: `${Math.min(100, (scoreVal / 0.2) * 100)}%`, height: '100%', background: 'var(--accent-primary)' }}></div>
                                          </div>
                                          <span style={{ fontFamily: 'monospace', width: '30px', textAlign: 'right' }}>{scoreVal.toFixed(2)}</span>
                                        </div>
                                      ))}
                                    </div>
                                  </div>
                                </div>
                              ) : (
                                <div className="flex-gap-12" style={{ color: 'var(--color-danger)', fontSize: '0.85rem' }}>
                                  <AlertTriangle size={14} />
                                  <span>{rolePlan.gap_reason}</span>
                                </div>
                              )}
                              
                              {opt && opt.swap_chain_summary && opt.swap_chain_summary.length > 0 && (
                                <div style={{ marginTop: '12px', padding: '10px 14px', background: 'rgba(245, 158, 11, 0.05)', border: '1px dashed rgba(245, 158, 11, 0.2)', borderRadius: '6px', fontSize: '0.8rem' }}>
                                  <strong style={{ color: 'var(--color-warning)', display: 'block', marginBottom: '2px' }}>Swap Backfill Sequence:</strong>
                                  <ul style={{ paddingLeft: '14px', color: 'var(--text-secondary)' }}>
                                    {opt.swap_chain_summary.map((step, stepIdx) => (
                                      <li key={stepIdx}>{step}</li>
                                    ))}
                                  </ul>
                                </div>
                              )}
                            </div>
                          );
                        })}
                      </div>
                    </div>
                  )}

                  {/* TAB 2: REPLACEMENT SWAP CHAINS */}
                  {modalTab === 'swap' && (
                    <div>
                      <div className="glass-panel" style={{ padding: '16px', marginBottom: '20px', background: 'var(--bg-tertiary)' }}>
                        <h4 style={{ color: 'var(--text-primary)', marginBottom: '4px', fontSize: '0.9rem' }}>Smart Swap Simulation</h4>
                        <p style={{ color: 'var(--text-secondary)', fontSize: '0.8rem', lineHeight: '1.4' }}>
                          If a candidate is currently allocated, we can pull them if backfilling is feasible and the source project health remains above {config?.impact?.critical_health_floor || 0.35}.
                        </p>
                      </div>

                      {recommendResult.project_plan.role_plans.some(rp => rp.recommended_option && rp.recommended_option.plan_type === 'B_SWAP') ? (
                        <div style={{ display: 'flex', flexDirection: 'column', gap: '20px' }}>
                          {recommendResult.project_plan.role_plans
                            .filter(rp => rp.recommended_option && rp.recommended_option.plan_type === 'B_SWAP')
                            .map((rolePlan) => {
                              const opt = rolePlan.recommended_option;
                              return (
                                <div key={rolePlan.role_id} style={{ background: '#fff', border: '1px solid var(--border-color)', padding: '20px', borderRadius: '10px' }}>
                                  <div style={{ marginBottom: '16px' }}>
                                    <h4 style={{ color: 'var(--text-primary)', fontSize: '0.95rem' }}>Swap Chain: {rolePlan.role_name}</h4>
                                    <span style={{ fontSize: '0.75rem', color: 'var(--text-secondary)' }}>
                                      Moving employee: <strong>{opt.recommended_employee_id}</strong>
                                    </span>
                                  </div>

                                  <div style={{ display: 'flex', flexDirection: 'column', gap: '12px' }}>
                                    <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
                                      <div style={{ background: 'var(--bg-tertiary)', border: '1px solid var(--border-color)', padding: '10px 14px', borderRadius: '6px', minWidth: '150px' }}>
                                        <div style={{ fontSize: '0.7rem', color: 'var(--accent-primary)', fontWeight: 'bold' }}>RESOURCE</div>
                                        <strong style={{ color: 'var(--text-primary)' }}>{opt.recommended_employee_id}</strong>
                                      </div>
                                      
                                      <ArrowRight size={18} style={{ color: 'var(--text-muted)' }} />

                                      <div style={{ background: 'var(--accent-primary-glow)', border: '1px solid rgba(79, 70, 229, 0.2)', padding: '10px 14px', borderRadius: '6px', minWidth: '150px' }}>
                                        <div style={{ fontSize: '0.7rem', color: 'var(--accent-primary)', fontWeight: 'bold' }}>TARGET PIPELINE</div>
                                        <strong style={{ color: 'var(--text-primary)' }}>{selectedProject.pipeline_id}</strong>
                                      </div>
                                    </div>

                                    {opt.swap_chain_summary && opt.swap_chain_summary.map((step, stepIdx) => (
                                      <div key={stepIdx} style={{ display: 'flex', alignItems: 'center', gap: '12px', marginLeft: '16px', borderLeft: '2px dashed var(--border-color)', paddingLeft: '16px' }}>
                                        <div style={{ background: 'var(--bg-primary)', border: '1px solid var(--border-color)', padding: '8px 12px', borderRadius: '6px', flex: 1, fontSize: '0.8rem' }}>
                                          <div style={{ fontSize: '0.7rem', color: 'var(--color-warning)', fontWeight: 'bold' }}>BACKFILL ACTION</div>
                                          <div style={{ color: 'var(--text-primary)', marginTop: '2px' }}>{step}</div>
                                        </div>
                                      </div>
                                    ))}
                                  </div>
                                </div>
                              );
                            })}
                        </div>
                      ) : (
                        <div style={{ padding: '30px', textAlign: 'center', color: 'var(--text-secondary)', background: '#fff', border: '1px solid var(--border-color)', borderRadius: '8px' }}>
                          <Info size={24} style={{ marginBottom: '8px', color: 'var(--text-muted)' }} />
                          <p style={{ fontSize: '0.85rem' }}>No replacement swap chains are required for this project plan.</p>
                        </div>
                      )}
                    </div>
                  )}

                  {/* TAB 3: AI COPILOT SUMMARY */}
                  {modalTab === 'summary' && (
                    <div className="animate-fade-in" style={{ display: 'flex', flexDirection: 'column', gap: '20px' }}>
                      <div style={{ background: '#fff', border: '1px solid var(--border-color)', padding: '20px', borderRadius: '10px' }}>
                        <h4 style={{ color: 'var(--text-primary)', marginBottom: '8px', fontSize: '0.95rem', display: 'flex', alignItems: 'center', gap: '8px' }}>
                          <Target size={16} style={{ color: 'var(--accent-primary)' }} /> Executive Plan Summary
                        </h4>
                        <p style={{ fontSize: '0.9rem', lineHeight: '1.5', color: 'var(--text-primary)' }}>
                          {recommendResult.llm_output.executive_summary}
                        </p>
                      </div>

                      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(320px, 1fr))', gap: '20px' }}>
                        <div style={{ background: '#fff', border: '1px solid var(--border-color)', padding: '20px', borderRadius: '10px' }}>
                          <h4 style={{ color: 'var(--text-primary)', marginBottom: '8px', fontSize: '0.95rem' }}>Staffing Selection Rationale</h4>
                          <div style={{ whiteSpace: 'pre-line', fontSize: '0.825rem', color: 'var(--text-secondary)', lineHeight: '1.5' }}>
                            {recommendResult.llm_output.recommendation_rationale}
                          </div>
                        </div>

                        <div style={{ background: '#fff', border: '1px solid var(--border-color)', padding: '20px', borderRadius: '10px' }}>
                          <h4 style={{ color: 'var(--text-primary)', marginBottom: '8px', fontSize: '0.95rem' }}>Risks & Assumptions</h4>
                          <div style={{ whiteSpace: 'pre-line', fontSize: '0.825rem', color: 'var(--text-secondary)', lineHeight: '1.5' }}>
                            {recommendResult.llm_output.risks_and_assumptions}
                          </div>
                        </div>
                      </div>

                      <div style={{ background: '#fff', border: '1px solid var(--border-color)', padding: '20px', borderRadius: '10px' }}>
                        <h4 style={{ color: 'var(--text-primary)', marginBottom: '8px', fontSize: '0.95rem' }}>Required Sourcing Actions</h4>
                        <div style={{ whiteSpace: 'pre-line', fontSize: '0.825rem', color: 'var(--text-secondary)', lineHeight: '1.5' }}>
                          {recommendResult.llm_output.rm_action_notes}
                        </div>
                      </div>
                    </div>
                  )}

                </div>
              </>
            )}
            
            {/* Modal Footer (Fixed) */}
            <div className="modal-footer" style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
              <div style={{ display: 'flex', gap: '12px' }}>
                {!loadingRecommend && recommendResult && (
                  <>
                    {isAllocated ? (
                      <button 
                        className="btn btn-secondary" 
                        style={{ background: 'rgba(239, 68, 68, 0.1)', color: 'var(--color-danger)', border: '1px solid rgba(239, 68, 68, 0.2)' }}
                        onClick={() => handleUndoAllocation(selectedProject.pipeline_id)}
                      >
                        Undo Allocation
                      </button>
                    ) : (
                      <button 
                        className="btn btn-primary" 
                        style={{ display: 'flex', alignItems: 'center', gap: '8px' }}
                        onClick={() => handleAllocateStaffing(selectedProject.pipeline_id)}
                      >
                        <Check size={14} /> Commit & Allocate Staffing
                      </button>
                    )}
                  </>
                )}
              </div>
              <button 
                className="btn btn-secondary" 
                onClick={() => { setSelectedProject(null); setRecommendResult(null); }}
              >
                Close Plan
              </button>
            </div>

          </div>
        </div>
      )}

    </div>
  );
}

export default App;
