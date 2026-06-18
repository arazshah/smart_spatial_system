import { useEffect, useMemo, useState } from "react";
import {
  createProject,
  getHealth,
  getMapLayers,
  getOutputManifest,
  getProject,
  getRequest,
  listProjects,
  listRequests,
  listUploads,
  runQuery,
  uploadFileByKind,
  listProjectDataSources,
  previewDataSource,
  updateDataSource,
  deleteDataSource
} from "./api/client";
import InspectorPanel from "./components/InspectorPanel";
import MapStage from "./components/MapStage";
import TopQueryBar from "./components/TopQueryBar";
import WorkbenchDrawer from "./components/WorkbenchDrawer";
import WorkbenchSidebar from "./components/WorkbenchSidebar";
import {
  getDataSourceBBox,
  getDataSourceCrs,
  getDataSourceCrsLabel,
  getDataSourceFeatureCount,
  getDataSourceGeometryType,
  getDataSourceKind,
  getDataSourceName,
  getDataSourcePropertyKeys,
  getDataSourceSize,
  getDataSourceStatus,
  getDataSourceTime,
  normalizeDataSource,
  normalizeDataSourceList,
} from "./lib/dataSources";
import Modal from "./components/Modal";
import DataSourcePreviewMap from "./components/DataSourcePreviewMap";
import { extractInlineGeoJsonLayers, mergeMapLayerPayloads } from "./utils/geojsonLayers";

function asList(payload, keys = []) {
  if (Array.isArray(payload)) return payload;

  for (const key of keys) {
    if (Array.isArray(payload?.[key])) return payload[key];
  }

  if (Array.isArray(payload?.items)) return payload.items;
  if (Array.isArray(payload?.data)) return payload.data;

  return [];
}

function normalizeResponseRecord(record) {
  if (!record) return null;
  return record.production_response || record.response || record.result || record;
}

function createRequestId() {
  return `req-ui-${Date.now()}-${Math.random().toString(16).slice(2, 8)}`;
}

function normalizeKind(upload) {
  const value = String(upload?.kind || "").toLowerCase();

  if (value.includes("vector")) return "vector";
  if (value.includes("raster")) return "raster";

  return "raster";
}

function inferBandMapFromQuery(query) {
  const text = String(query || "").toLowerCase();

  if (
    text.includes("ndvi") ||
    text.includes("vegetation") ||
    text.includes("پوشش گیاهی") ||
    text.includes("گیاهی")
  ) {
    return {
      red: 1,
      nir: 2,
    };
  }

  return {};
}


function formatPreviewBytes(value) {
  const bytes = Number(value || 0);

  if (!Number.isFinite(bytes) || bytes <= 0) return "—";

  const units = ["B", "KB", "MB", "GB", "TB"];
  let size = bytes;
  let unitIndex = 0;

  while (size >= 1024 && unitIndex < units.length - 1) {
    size /= 1024;
    unitIndex += 1;
  }

  return `${size.toFixed(size >= 10 || unitIndex === 0 ? 0 : 1)} ${units[unitIndex]}`;
}

function formatPreviewDateTime(value) {
  if (!value) return "—";

  const date = new Date(value);

  if (Number.isNaN(date.getTime())) return "—";

  return new Intl.DateTimeFormat("fa-IR", {
    dateStyle: "short",
    timeStyle: "short",
  }).format(date);
}

function getPreviewPropertyKeys(payload) {
  return getDataSourcePropertyKeys(payload);
}

function getPreviewFeatureCount(payload) {
  return getDataSourceFeatureCount(payload);
}

function getPreviewCrs(payload) {
  return getDataSourceCrsLabel(payload) || "—";
}

function getPreviewBBox(payload) {
  const value = getDataSourceBBox(payload);

  if (!value) return "—";
  if (Array.isArray(value)) return value.join(", ");
  return String(value);
}

function getPreviewCrsLabel(payload) {
  return getDataSourceCrsLabel(payload);
}

function getPreviewRasterShape(payload) {
  const width =
    payload?.preview?.width ??
    payload?.metadata?.width ??
    payload?.summary?.width ??
    null;

  const height =
    payload?.preview?.height ??
    payload?.metadata?.height ??
    payload?.summary?.height ??
    null;

  if (width && height) return `${width} × ${height}`;
  if (width) return `${width}`;
  if (height) return `${height}`;
  return "—";
}

function getPreviewBandCount(payload) {
  return (
    payload?.preview?.band_count ??
    payload?.metadata?.band_count ??
    payload?.summary?.band_count ??
    payload?.bands ??
    null
  );
}


function normalizeHistoryStatus(value) {
  const status = String(value || "").toLowerCase();

  if (["succeeded", "success", "completed", "done"].includes(status)) {
    return "succeeded";
  }

  if (["failed", "error"].includes(status)) {
    return "failed";
  }

  if (["running", "pending", "analyzing"].includes(status)) {
    return "running";
  }

  return status || "unknown";
}

function makeRequestRecord(result, payload, command, project) {
  const requestId =
    result?.request_id ||
    result?.id ||
    payload?.request_id ||
    command?.request_id ||
    null;

  if (!requestId) return null;

  const query =
    command?.query ||
    payload?.query ||
    payload?.question ||
    result?.query ||
    result?.metadata?.original_query ||
    result?.request?.query ||
    "";

  const rawStatus =
    result?.status ||
    result?.run_result?.status ||
    (result?.ok === true ? "succeeded" : result?.ok === false ? "failed" : "");

  const status = normalizeHistoryStatus(rawStatus);

  return {
    ...(typeof result === "object" && result ? result : {}),
    request_id: requestId,
    project_id: project?.project_id || result?.project_id || payload?.project_id || null,
    query,
    question: query,
    status,
    created_at:
      result?.created_at ||
      result?.timestamp ||
      result?.metadata?.created_at ||
      new Date().toISOString(),
    updated_at: new Date().toISOString(),
  };
}

function upsertRequestById(items, record) {
  if (!record?.request_id) return items || [];

  const list = Array.isArray(items) ? items : [];
  const index = list.findIndex((item) => item.request_id === record.request_id);

  if (index === -1) {
    return [record, ...list];
  }

  const next = [...list];
  next[index] = {
    ...next[index],
    ...record,
  };

  return next;
}

function attachRequestToProject(project, requestId) {
  if (!project?.project_id || !requestId) return project;

  const requests = Array.isArray(project.requests) ? project.requests : [];

  if (requests.includes(requestId)) {
    return project;
  }

  return {
    ...project,
    requests: [requestId, ...requests],
  };
}

export default function App() {
  const [health, setHealth] = useState(null);
  const [projects, setProjects] = useState([]);
  const [activeProject, setActiveProject] = useState(null);

  const [uploads, setUploads] = useState([]);
  const [selectedUpload, setSelectedUpload] = useState(null);

  const [requests, setRequests] = useState([]);
  const [activeRequest, setActiveRequest] = useState(null);

  const [response, setResponse] = useState(null);
  const [mapLayers, setMapLayers] = useState(null);
  const [outputManifest, setOutputManifest] = useState(null);

  const [layerWorkspace, setLayerWorkspace] = useState({
    hiddenLayerKeys: new Set(),
    removedLayerKeys: new Set(),
    layerStyles: {},
    fitRequest: { key: null, trigger: 0 },
    styleLayerKey: null,
  });

  const [activeTool, setActiveTool] = useState(null);
  const [loading, setLoading] = useState(false);
  const [bootLoading, setBootLoading] = useState(true);
  const [globalError, setGlobalError] = useState("");

  const [previewModalData, setPreviewModalData] = useState(null);
  const [editModalUpload, setEditModalUpload] = useState(null);
  const [deleteModalUpload, setDeleteModalUpload] = useState(null);

  const [editName, setEditName] = useState("");
  const [editDescription, setEditDescription] = useState("");
  const [editTags, setEditTags] = useState("");

  const [modalBusy, setModalBusy] = useState(false);
  const [modalError, setModalError] = useState("");

  async function refreshProjects(preferredProjectId = null) {
    const payload = await listProjects();
    const items = asList(payload, ["projects"]);
    setProjects(items);

    const nextProjectId =
      preferredProjectId ||
      activeProject?.project_id ||
      items[0]?.project_id ||
      null;

    if (nextProjectId) {
      const full = await getProject(nextProjectId);
      setActiveProject(full);
    } else {
      setActiveProject(null);
    }
  }

  async function refreshUploads(projectOverride = null) {
    const projectId = projectOverride?.project_id || activeProject?.project_id;

    if (projectId) {
      const payload = await listProjectDataSources(projectId);
      setUploads(normalizeDataSourceList(asList(payload, ["uploads"])));
      return;
    }

    const payload = await listUploads();
    setUploads(asList(payload, ["uploads"]));
  }

  async function refreshRequests() {
    const payload = await listRequests();
    setRequests(asList(payload, ["requests"]));
  }

  async function bootstrap() {
    try {
      setBootLoading(true);
      setGlobalError("");

      const healthPayload = await getHealth();
      setHealth(healthPayload);

      await refreshProjects();
      await refreshUploads();
      await refreshRequests();
    } catch (err) {
      setGlobalError(err.message);
    } finally {
      setBootLoading(false);
    }
  }

  useEffect(() => {
    bootstrap();
  }, []);

  useEffect(() => {
    if (!activeProject?.uploads?.length) {
      setSelectedUpload(null);
      return;
    }

    if (
      selectedUpload?.upload_id &&
      !activeProject.uploads.includes(selectedUpload.upload_id)
    ) {
      setSelectedUpload(null);
    }
  }, [activeProject, selectedUpload]);

  function findBestUploadForActiveProject() {
    if (!activeProject?.uploads?.length) return null;

    if (
      selectedUpload?.upload_id &&
      activeProject.uploads.includes(selectedUpload.upload_id)
    ) {
      return selectedUpload;
    }

    const projectUploadIds = new Set(activeProject.uploads);

    return uploads.find((item) => projectUploadIds.has(item.upload_id)) || null;
  }

  function buildSmartPayload(command) {
    if (!activeProject?.project_id) {
      throw new Error("ابتدا از منوی Projects یک پروژه بسازید یا انتخاب کنید.");
    }

    const bestUpload = findBestUploadForActiveProject();
    const kind = normalizeKind(bestUpload);

    const inputs = bestUpload
      ? kind === "vector"
        ? { vector_ref: bestUpload.upload_id }
        : { raster_ref: bestUpload.upload_id }
      : {};

    return {
      query: command.query,
      request_id: createRequestId(),
      inputs,
      band_map: inferBandMapFromQuery(command.query),
      metadata: {
        project_id: activeProject.project_id,
        selected_upload_id: bestUpload?.upload_id || null,
        intelligent_input_selection: true,
      },
      user_context: {
        source: "minimal-intelligent-workbench",
        input_selection_mode: bestUpload ? "auto_project_data" : "no_explicit_data",
      },
    };
  }

  async function handleCreateProject(payload) {
    const created = await createProject(payload);
    await refreshProjects(created.project_id);
    setActiveTool(null);
    return created;
  }

  async function handleSelectProject(project) {
    const full = await getProject(project.project_id);
    setActiveProject(full);
    setSelectedUpload(null);
    setActiveRequest(null);
    setResponse(null);
    setMapLayers(null);
    setOutputManifest(null);
  }

  async function handleUploadFile(kind, file) {
    if (!activeProject?.project_id) {
      throw new Error("ابتدا پروژه فعال انتخاب کنید.");
    }

    const uploaded = await uploadFileByKind(kind, file, activeProject.project_id);

    await refreshUploads();

    const full = await getProject(activeProject.project_id);
    setActiveProject(full);
    setSelectedUpload(uploaded);

    return uploaded;
  }

  async function loadRequestWorkspace(requestId) {
    const record = await getRequest(requestId);
    const normalized = normalizeResponseRecord(record);

    setActiveRequest(record);
    setResponse(normalized);

    try {
      const [layers, outputs] = await Promise.all([
        getMapLayers(requestId),
        getOutputManifest(requestId),
      ]);

      setMapLayers((previous) => mergeMapLayerPayloads(previous, layers));
      setOutputManifest(outputs);
    } catch {
      setMapLayers(null);
      setOutputManifest(null);
    }
  }

  async function handleSelectRequest(request) {
    await loadRequestWorkspace(request.request_id);
    setActiveTool(null);
  }

  async function handleRunQuery(command) {
    try {
      setLoading(true);
      setGlobalError("");
      setResponse(null);
      setMapLayers(null);
      setOutputManifest(null);

      const payload = buildSmartPayload(command);

      const pendingRecord = makeRequestRecord(
        {
          request_id: payload.request_id,
          status: "running",
        },
        payload,
        command,
        activeProject,
      );

      if (pendingRecord?.request_id) {
        setActiveRequest(pendingRecord);
        setRequests((previous) => upsertRequestById(previous, pendingRecord));
        setActiveProject((previous) =>
          attachRequestToProject(previous, pendingRecord.request_id),
        );
      }

      const result = await runQuery(payload);
      const inlineLayers = extractInlineGeoJsonLayers(result);

      if (inlineLayers.layers.length) {
        setMapLayers((previous) => mergeMapLayerPayloads(inlineLayers, previous));
      }

      const completedRecord = makeRequestRecord(
        result,
        payload,
        command,
        activeProject,
      );

      setResponse(result);
      setActiveRequest(completedRecord || result);

      if (completedRecord?.request_id) {
        setRequests((previous) => upsertRequestById(previous, completedRecord));
        setActiveProject((previous) =>
          attachRequestToProject(previous, completedRecord.request_id),
        );
      }

      if (result?.request_id) {
        try {
          const [layers, outputs] = await Promise.all([
            getMapLayers(result.request_id),
            getOutputManifest(result.request_id),
          ]);

          setMapLayers((previous) => mergeMapLayerPayloads(previous, layers));
          setOutputManifest(outputs);
        } catch {
          // Keep inline GeoJSON layers extracted from the main /query response.
          // /map-layers or /outputs may not exist for direct handlers.
          setOutputManifest(null);
        }
      }

      await refreshRequests();

      if (payload?.metadata?.project_id) {
        const full = await getProject(payload.metadata.project_id);
        setActiveProject(full);
      }
    } catch (err) {
      setGlobalError(err.message);
      setResponse({
        status: "failed",
        answer: err.message,
      });
    } finally {
      setLoading(false);
    }
  }

  const outputFiles = useMemo(() => {
    return outputManifest?.files || [];
  }, [outputManifest]);

  if (bootLoading) {
    return <div className="wb-loader">در حال آماده‌سازی محیط کاری...</div>;
  }


  async function handlePreviewUpload(upload) {
    if (!upload?.upload_id) return null;

    setActiveTool(null);
    setModalError("");
    setModalBusy(true);

    try {
      const payload = await previewDataSource(upload.upload_id);
      const merged = normalizeDataSource({
        ...upload,
        ...payload,
        preview: payload?.preview || upload?.preview,
      });

      setUploads((prev) =>
        prev.map((item) =>
          item?.upload_id === upload.upload_id ? merged : item
        )
      );

      setSelectedUpload((prev) =>
        prev?.upload_id === upload.upload_id ? merged : prev
      );

      setPreviewModalData(merged);
      return merged;
    } catch (err) {
      setModalError(err.message);
      throw err;
    } finally {
      setModalBusy(false);
    }
  }

  async function handleEditUpload(upload) {
    if (!upload?.upload_id) return null;

    setActiveTool(null);
    setModalError("");
    setEditModalUpload(upload);
    setEditName(
      upload?.display_name ||
      upload?.name ||
      upload?.filename ||
      ""
    );
    setEditDescription(upload?.description || "");
    setEditTags(Array.isArray(upload?.tags) ? upload.tags.join(", ") : "");
    return upload;
  }

  async function handleSaveEditModal() {
    if (!editModalUpload?.upload_id) return null;

    const trimmedName = String(editName || "").trim();

    if (!trimmedName) {
      setModalError("نام منبع داده نمی‌تواند خالی باشد.");
      return null;
    }

    setModalError("");
    setModalBusy(true);

    try {
      const tags = String(editTags || "")
        .split(",")
        .map((item) => item.trim())
        .filter(Boolean);

      const updated = await updateDataSource(editModalUpload.upload_id, {
        name: trimmedName,
        description: String(editDescription || "").trim(),
        tags,
      });

      await refreshUploads();

      setSelectedUpload((prev) =>
        prev?.upload_id === editModalUpload.upload_id ? updated : prev
      );

      setPreviewModalData((prev) =>
        prev?.upload_id === editModalUpload.upload_id
          ? { ...prev, ...updated }
          : prev
      );

      setEditModalUpload(null);
      setEditName("");
      setEditDescription("");
      setEditTags("");

      return updated;
    } catch (err) {
      setModalError(err.message);
      throw err;
    } finally {
      setModalBusy(false);
    }
  }

  async function handleDeleteUpload(upload) {
    if (!upload?.upload_id) return null;

    setActiveTool(null);
    setModalError("");
    setDeleteModalUpload(upload);
    return upload;
  }

  async function handleConfirmDeleteModal() {
    if (!deleteModalUpload?.upload_id) return null;

    setModalError("");
    setModalBusy(true);

    try {
      await deleteDataSource(deleteModalUpload.upload_id);

      if (selectedUpload?.upload_id === deleteModalUpload.upload_id) {
        setSelectedUpload(null);
      }

      if (previewModalData?.upload_id === deleteModalUpload.upload_id) {
        setPreviewModalData(null);
      }

      if (activeProject?.project_id) {
        const full = await getProject(activeProject.project_id);
        setActiveProject(full);
        await refreshUploads(full);
      } else {
        await refreshUploads();
      }

      setDeleteModalUpload(null);
      return true;
    } catch (err) {
      setModalError(err.message);
      throw err;
    } finally {
      setModalBusy(false);
    }
  }

  function closeAllModals() {
    if (modalBusy) return;

    setPreviewModalData(null);
    setEditModalUpload(null);
    setDeleteModalUpload(null);
    setModalError("");
  }

  return (
    <div className="workbench-shell">
      <WorkbenchSidebar
        activeTool={activeTool}
        onSelectTool={setActiveTool}
        health={health}
      />

      <WorkbenchDrawer
        activeTool={activeTool}
        onClose={() => setActiveTool(null)}
        projects={projects}
        activeProject={activeProject}
        onCreateProject={handleCreateProject}
        onSelectProject={handleSelectProject}
        uploads={uploads}
        selectedUpload={selectedUpload}
        onSelectUpload={(upload) => {
          setSelectedUpload(upload);
          setActiveTool(null);
        }}
        onUploadFile={handleUploadFile}
        requests={requests}
        activeRequest={activeRequest}
        onSelectRequest={handleSelectRequest}
        health={health}
        onPreviewUpload={handlePreviewUpload}
        onEditUpload={handleEditUpload}
        onDeleteUpload={handleDeleteUpload}
      />

      <main className="workbench-main">
        {globalError && <div className="global-toast error">{globalError}</div>}

        <TopQueryBar
          project={activeProject}
          uploads={uploads}
          selectedUpload={selectedUpload}
          onRun={handleRunQuery}
          loading={loading}
          onOpenProjects={() => setActiveTool("projects")}
          onOpenUploads={() => setActiveTool("uploads")}
        />

        <MapStage
          project={activeProject}
          selectedUpload={selectedUpload}
          mapLayers={mapLayers}
          loading={loading}
          layerWorkspace={layerWorkspace}
          onLayerWorkspaceChange={setLayerWorkspace}
        />
      </main>

      <InspectorPanel
        response={response}
        mapLayers={mapLayers}
        outputManifest={outputManifest}
        activeRequest={activeRequest}
        loading={loading}
        error={globalError}
        layerWorkspace={layerWorkspace}
        onLayerWorkspaceChange={setLayerWorkspace}
      />

      <Modal
        open={Boolean(previewModalData)}
        title="Data Source Preview"
        subtitle={getDataSourceName(previewModalData)}
        onClose={closeAllModals}
        size="lg"
        footer={
          <button type="button" onClick={closeAllModals} disabled={modalBusy}>
            بستن
          </button>
        }
      >
        {modalError ? <div className="alert error">{modalError}</div> : null}

        <div className="modal-info-grid">
          <span>
            <small>Kind</small>
            <b>{getDataSourceKind(previewModalData)}</b>
          </span>
          <span>
            <small>Source</small>
            <b>{previewModalData?.source_type || "file"}</b>
          </span>
          <span>
            <small>Status</small>
            <b>{getDataSourceStatus(previewModalData)}</b>
          </span>
          <span>
            <small>Format</small>
            <b>{previewModalData?.extension || "—"}</b>
          </span>
          <span>
            <small>Size</small>
            <b>{formatPreviewBytes(getDataSourceSize(previewModalData))}</b>
          </span>
          <span>
            <small>Created</small>
            <b>{formatPreviewDateTime(previewModalData?.created_at)}</b>
          </span>
          <span>
            <small>Updated</small>
            <b>{formatPreviewDateTime(getDataSourceTime(previewModalData))}</b>
          </span>
          <span>
            <small>File</small>
            <b>{previewModalData?.filename || previewModalData?.original_filename || "—"}</b>
          </span>
        </div>

        {previewModalData?.description ? (
          <div className="modal-section">
            <h4>Description</h4>
            <p>{previewModalData.description}</p>
          </div>
        ) : null}

        {Array.isArray(previewModalData?.tags) && previewModalData.tags.length ? (
          <div className="modal-section">
            <h4>Tags</h4>
            <div className="chip-row">
              {previewModalData.tags.map((tag) => (
                <span key={tag} className="chip">
                  {tag}
                </span>
              ))}
            </div>
          </div>
        ) : null}

        <div className="modal-section">
          <h4>Dataset Summary</h4>
          <div className="modal-info-grid">
            <span>
              <small>Geometry</small>
              <b>{getDataSourceGeometryType(previewModalData) || "—"}</b>
            </span>
            <span>
              <small>Features</small>
              <b>{getPreviewFeatureCount(previewModalData) ?? "—"}</b>
            </span>
            <span>
              <small>CRS</small>
              <b>{getPreviewCrsLabel(previewModalData)}</b>
            </span>
            <span>
              <small>BBox</small>
              <b>{getPreviewBBox(previewModalData)}</b>
            </span>
            <span>
              <small>Raster Size</small>
              <b>{getPreviewRasterShape(previewModalData)}</b>
            </span>
            <span>
              <small>Bands</small>
              <b>{getPreviewBandCount(previewModalData) ?? "—"}</b>
            </span>
          </div>

          {getPreviewPropertyKeys(previewModalData).length ? (
            <div className="modal-subsection">
              <small>Property Keys</small>
              <div className="chip-row">
                {getPreviewPropertyKeys(previewModalData).slice(0, 20).map((key) => (
                  <span key={key} className="chip">
                    {key}
                  </span>
                ))}
              </div>
            </div>
          ) : (
            <p className="muted-note">
              {getDataSourceKind(previewModalData) === "raster"
                ? "Preview محتوایی برای فایل رستری هنوز محدود است، اما metadata فایل در دسترس است."
                : "اطلاعات summary بیشتری برای این منبع داده موجود نیست."}
            </p>
          )}
        </div>

        <DataSourcePreviewMap payload={previewModalData} />

        <details className="modal-section">
          <summary>Raw Preview JSON</summary>
          <div className="modal-json-block">
            <pre>{JSON.stringify(previewModalData?.preview || previewModalData || {}, null, 2)}</pre>
          </div>
        </details>
      </Modal>

      <Modal
        open={Boolean(editModalUpload)}
        title="Edit Data Source"
        subtitle={
          editModalUpload?.display_name ||
          editModalUpload?.name ||
          editModalUpload?.filename ||
          editModalUpload?.upload_id
        }
        onClose={closeAllModals}
        footer={
          <>
            <button type="button" onClick={closeAllModals} disabled={modalBusy}>
              انصراف
            </button>
            <button type="button" onClick={handleSaveEditModal} disabled={modalBusy}>
              {modalBusy ? "در حال ذخیره..." : "ذخیره تغییرات"}
            </button>
          </>
        }
      >
        <div className="modal-form">

          {editModalUpload && (
            <div className="modal-ds-summary">
              <div className="modal-ds-summary-row">
                <span className="modal-ds-meta-item">
                  <small>Kind</small>
                  <b>{getDataSourceKind(editModalUpload) || "—"}</b>
                </span>
                <span className="modal-ds-meta-item">
                  <small>Status</small>
                  <b>{getDataSourceStatus(editModalUpload) || "—"}</b>
                </span>
                <span className="modal-ds-meta-item">
                  <small>Size</small>
                  <b>{formatPreviewBytes(getDataSourceSize(editModalUpload))}</b>
                </span>
                <span className="modal-ds-meta-item">
                  <small>Features</small>
                  <b>{getDataSourceFeatureCount(editModalUpload) ?? "—"}</b>
                </span>
                <span className="modal-ds-meta-item">
                  <small>Geometry</small>
                  <b>{getDataSourceGeometryType(editModalUpload) || "—"}</b>
                </span>
                <span className="modal-ds-meta-item">
                  <small>CRS</small>
                  <b>{getDataSourceCrsLabel(editModalUpload)}</b>
                </span>
              </div>

              {(editModalUpload?.filename || editModalUpload?.original_filename) && (
                <div className="modal-ds-filename">
                  <small>File:</small>
                  <span dir="ltr">
                    {editModalUpload.filename || editModalUpload.original_filename}
                  </span>
                </div>
              )}
            </div>
          )}

          <label>
            نام نمایشی
            <input
              value={editName}
              onChange={(event) => setEditName(event.target.value)}
              placeholder="Display name"
              disabled={modalBusy}
            />
          </label>

          <label>
            توضیح
            <textarea
              rows={3}
              value={editDescription}
              onChange={(event) => setEditDescription(event.target.value)}
              placeholder="اختیاری"
              disabled={modalBusy}
            />
          </label>

          <label>
            تگ‌ها
            <input
              value={editTags}
              onChange={(event) => setEditTags(event.target.value)}
              placeholder="sample, tehran, vector"
              disabled={modalBusy}
            />
            <small className="field-hint">
              تگ‌ها را با کاما جدا کنید — مثلاً: tehran, vector, 2024
            </small>
          </label>

          {modalError ? <div className="alert error">{modalError}</div> : null}
        </div>
      </Modal>

      <Modal
        open={Boolean(deleteModalUpload)}
        title="Delete Data Source"
        subtitle={
          deleteModalUpload?.display_name ||
          deleteModalUpload?.name ||
          deleteModalUpload?.filename ||
          deleteModalUpload?.upload_id
        }
        onClose={closeAllModals}
        footer={
          <>
            <button type="button" onClick={closeAllModals} disabled={modalBusy}>
              انصراف
            </button>
            <button
              type="button"
              className="danger"
              onClick={handleConfirmDeleteModal}
              disabled={modalBusy}
            >
              {modalBusy ? "در حال حذف..." : "حذف"}
            </button>
          </>
        }
      >
        <div className="modal-delete-copy">
          <p>آیا از حذف این منبع داده مطمئن هستید؟</p>
          <ul>
            <li>از storage حذف می‌شود</li>
            <li>از پروژه فعال جدا می‌شود</li>
            <li>این عملیات قابل بازگشت نیست</li>
          </ul>

          {modalError ? <div className="alert error">{modalError}</div> : null}
        </div>
      </Modal>
    </div>
  );
}
