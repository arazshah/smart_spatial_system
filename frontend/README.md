# Smart Spatial System — frontend

React + Vite workbench for the Smart Spatial System API: create projects, upload or connect data sources, ask questions in natural language, watch the plan execute, and inspect the resulting map layers, tables, reports and files on a Leaflet map.

## Run

```bash
cp .env.example .env        # VITE_API_BASE_URL, default http://127.0.0.1:8000
npm install
npm run dev                 # http://localhost:5173
```

Start the backend first (see the [main README](../README.md)).

## Main panels

| Area | Components |
|---|---|
| Query | `TopQueryBar`, `QueryPanel`, `QueryProgressStepper`, `RealtimeProgressModal` |
| Results | `MapStage` / `MapViewer`, `ResponsePanel`, `InspectorPanel`, `OutputFilesPanel` |
| Data | `ProjectPanel`, `UploadPanel`, `UploadBrowser`, `DataSourcePreviewMap` |
| System | `PluginManagerPanel`, `PluginConfigModal`, `WeightsPanel`, `SettingsPanel`, `RequestsPanel`, `FeedbackPanel` |

The API client lives in `src/api/client.js`.
