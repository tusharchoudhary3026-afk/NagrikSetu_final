"""Area-level complaint map for the staff member's authorized scope."""
from collections import Counter
from html import escape

import folium
import streamlit as st
from folium.plugins import MarkerCluster
from streamlit_folium import st_folium

from core.staff_dashboard import list_staff_cases
from core.ui import apply_demo_style, render_hero, require_permission

# Multi-city coordinates across Madhya Pradesh (Gwalior, Bhopal, Indore, Jabalpur, Ujjain)
AREA_CENTERS = {
    # Gwalior
    "lashkar": (26.2150, 78.1650),
    "thatipur": (26.2250, 78.2000),
    "morar": (26.2200, 78.2150),
    "gole ka mandir": (26.2300, 78.1960),
    "city centre": (26.2050, 78.1800),
    "city center": (26.2050, 78.1800),
    "hazira": (26.2370, 78.1750),
    "jayendraganj": (26.2120, 78.1720),
    # Bhopal
    "mp nagar": (23.2332, 77.4343),
    "kolar road": (23.1783, 77.4182),
    "arera colony": (23.2135, 77.4326),
    "tt nagar": (23.2384, 77.4011),
    "karond": (23.2980, 77.4020),
    # Indore
    "vijay nagar": (22.7533, 75.8937),
    "palasia": (22.7244, 75.8839),
    "rajwada": (22.7196, 75.8577),
    "bhanwarkuan": (22.6926, 75.8676),
    "rau": (22.6280, 75.8170),
    # Jabalpur
    "civil lines": (23.1610, 79.9489),
    "wright town": (23.1670, 79.9320),
    "russell chowk": (23.1700, 79.9410),
    # Ujjain
    "freeganj": (23.1789, 75.7955),
    "nanakheda": (23.1550, 75.7830),
    "mahakal marg": (23.1830, 75.7680),
}
DEFAULT_CITY_CENTER = (26.2183, 78.1828)

user = require_permission("can_view_hotspots")
apply_demo_style()
render_hero(
    "Municipal GIS",
    "Geo Map & Hotspot Inspector",
    "Explore complaint counts by area within your authorized staff scope.",
)

try:
    all_cases = list_staff_cases(user)
except (ValueError, PermissionError) as exc:
    st.error(str(exc))
    st.stop()

if not all_cases:
    st.info("No complaint records found in your authorized scope.")
    st.stop()

st.info(
    "Approximate area view: complaints do not contain GPS coordinates. "
    "Markers show illustrative area centers, not exact complaint locations or official boundaries."
)


def case_area(case):
    return str(case.get("area_name") or case.get("location") or "Unknown area").strip() or "Unknown area"


st.sidebar.header("Map Filters")
area_options = sorted({case_area(case) for case in all_cases}, key=str.casefold)
category_options = sorted(
    {str(case.get("issue") or "Uncategorized") for case in all_cases},
    key=str.casefold,
)
selected_area = st.sidebar.selectbox("Filter by Ward / Area", ["All areas", *area_options])
selected_category = st.sidebar.selectbox("Filter by Category", ["All categories", *category_options])

filtered_cases = [
    case for case in all_cases
    if (selected_area == "All areas" or case_area(case) == selected_area)
    and (selected_category == "All categories"
         or str(case.get("issue") or "Uncategorized") == selected_category)
]
st.caption(f"Showing **{len(filtered_cases)}** in-scope complaints matching these filters.")

area_counts = Counter(case_area(case) for case in filtered_cases)
mapped_areas = {
    area: count for area, count in area_counts.items()
    if area.casefold() in AREA_CENTERS
}
unmapped_count = sum(count for area, count in area_counts.items() if area not in mapped_areas)

# Calculate dynamic center based on active mapped markers
if mapped_areas:
    coords = [AREA_CENTERS[area.casefold()] for area in mapped_areas]
    center_lat = sum(c[0] for c in coords) / len(coords)
    center_lon = sum(c[1] for c in coords) / len(coords)
    active_center = (center_lat, center_lon)
else:
    active_center = DEFAULT_CITY_CENTER

map_view = folium.Map(location=active_center, zoom_start=12, tiles="OpenStreetMap")
marker_cluster = MarkerCluster(name="Approximate area counts").add_to(map_view)
for area, count in sorted(mapped_areas.items(), key=lambda item: item[0].casefold()):
    lat, lon = AREA_CENTERS[area.casefold()]
    safe_area = escape(area)
    popup = folium.Popup(
        f'<div><strong>Area:</strong> {safe_area}<br>'
        f'<strong>Matching complaints:</strong> {count}<br>'
        '<em>Approximate area center; not an exact case location.</em></div>',
        max_width=280,
    )
    folium.Marker(
        location=(lat, lon),
        popup=popup,
        tooltip=f"{safe_area} · {count} complaints (approximate area)",
        icon=folium.Icon(color="blue", icon="info-sign"),
    ).add_to(marker_cluster)

st_folium(map_view, width=None, height=520, use_container_width=True)
if unmapped_count:
    st.caption(
        f"{unmapped_count} matching complaint(s) have areas without a configured demo center "
        "and are not plotted."
    )

col1, col2, col3 = st.columns(3)
col1.metric("Complaints in scope", len(filtered_cases))
col2.metric("Areas plotted", len(mapped_areas))
col3.metric("Active area filter", selected_area)

if area_counts:
    st.subheader("Complaint counts by area")
    st.dataframe(
        [
            {"Area": area, "Matching complaints": count,
             "Map position": "Approximate" if area.casefold() in AREA_CENTERS else "Not plotted"}
            for area, count in sorted(area_counts.items(), key=lambda item: item[0].casefold())
        ],
        hide_index=True,
        use_container_width=True,
    )