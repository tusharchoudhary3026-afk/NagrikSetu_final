import streamlit as st
import folium
from streamlit_folium import st_folium
from folium.plugins import MarkerCluster
from core.staff_dashboard import list_staff_cases
from core.ui import apply_demo_style, render_hero, require_permission

# 1. Access Control & UI styling
user = require_permission("can_view_hotspots")  # Using authorized staff permission
apply_demo_style()

render_hero("Municipal GIS", "Geo Map & Hotspot Inspector", "Visualize complaint distributions across geographic wards and coordinate field inspections.")

# 2. Fetch authorized cases
try:
    all_cases = list_staff_cases(user)
except (ValueError, PermissionError) as exc:
    st.error(str(exc))
    st.stop()

if not all_cases:
    st.info("No complaint records found in your authorized scope.")
    st.stop()

# 3. Sidebar Filters
st.sidebar.header("Map Filters")
area_options = sorted({case.get("area_name") or case.get("location") or "Unknown area" for case in all_cases}, key=str.casefold)
category_options = sorted({case.get("issue") or "Uncategorized" for case in all_cases}, key=str.casefold)

selected_area = st.sidebar.selectbox("Filter by Ward / Area", ["All areas", *area_options])
selected_category = st.sidebar.selectbox("Filter by Category", ["All categories", *category_options])

# Filter cases based on sidebar selection
filtered_cases = all_cases
if selected_area != "All areas":
    filtered_cases = [c for c in filtered_cases if (c.get("area_name") or c.get("location")) == selected_area]
if selected_category != "All categories":
    filtered_cases = [c for c in filtered_cases if (c.get("issue") or "Uncategorized") == selected_category]

st.caption(f"Showing **{len(filtered_cases)}** complaints on the map matching current filters.")

# 4. Initialize Folium Map (Centered around Gwalior default coordinates)
# Default center: Gwalior coordinates (26.2183, 78.1828)
m = folium.Map(location=[26.2183, 78.1828], zoom_start=13, tiles="OpenStreetMap")
marker_cluster = MarkerCluster().add_to(m)

# Fallback coordinate offsets for mock data visualization if latitude/longitude are missing in DB
# (This ensures every complaint gets neatly pinned around Gwalior wards during demo)
ward_coords = {
    "Lashkar": (26.2150, 78.1650),
    "Thatipur": (26.2250, 78.2000),
    "Morar": (26.2200, 78.2150),
    "Huzur": (26.2183, 78.1828),
    "City Center": (26.2050, 78.1800)
}

plotted_count = 0
for idx, case in enumerate(filtered_cases):
    area = case.get("area_name") or case.get("location") or "Lashkar"
    
    # Use real coordinates if available in case dict, otherwise fallback to ward mock coords with slight offset
    lat = case.get("latitude")
    lon = case.get("longitude")
    
    if not lat or not lon:
        base_lat, base_lon = ward_coords.get(area, (26.2183, 78.1828))
        # Add a tiny pseudo-random offset based on index so markers don't overlap completely
        lat = base_lat + (idx % 5) * 0.0012
        lon = base_lon + (idx % 7) * 0.0012

    status = case.get("status", "Pending")
    color = "red" if status.lower() in ["pending", "open"] else "orange" if "progress" in status.lower() else "green"

    popup_html = f"""
    <div style="font-family: sans-serif; font-size: 12px; width: 200px;">
        <b>Case ID:</b> {case.get('case_id')}<br>
        <b>Area:</b> {area}<br>
        <b>Issue:</b> {case.get('issue') or 'General Grievance'}<br>
        <b>Status:</b> {status}<br>
        <hr style="margin: 5px 0;">
        <i>Click case details in sidebar or main portal to take action.</i>
    </div>
    """

    folium.Marker(
        location=[lat, lon],
        popup=folium.Popup(popup_html, max_width=250),
        tooltip=f"{case.get('case_id')} · {area}",
        icon=folium.Icon(color=color, icon="info-sign")
    ).add_to(marker_cluster)
    plotted_count += 1

# 5. Render Map in Streamlit
st_folium(m, width=1100, height=550)

# 6. Quick Metric Summary Below Map
col1, col2, col3 = st.columns(3)
col1.metric("Total Mapped Points", plotted_count)
col2.metric("Active Ward Scope", selected_area)
col3.metric("Map Engine", "Leaflet / OpenStreetMap")