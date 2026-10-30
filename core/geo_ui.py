"""Streamlit cascading selectors backed by the editable geography JSON file."""
import streamlit as st

from .geo import area_options


def render_geography_picker(prefix: str, *, key_suffix: str = "",
                            allow_unknown: bool = False) -> tuple[str, str, str] | tuple[None, None, None]:
    options = area_options()
    state_codes = list(options)
    display_states = ([None] + state_codes) if allow_unknown else state_codes
    state_code = st.selectbox("State", display_states,
                              format_func=lambda code: "Outside listed service areas" if code is None else options[code]["name"],
                              key=f"{prefix}-state{key_suffix}")
    if state_code is None:
        return None, None, None
    cities = options[state_code]["cities"]
    city_codes = list(cities)
    display_cities = ([None] + city_codes) if allow_unknown else city_codes
    city_code = st.selectbox("City / district", display_cities,
                             format_func=lambda code: "Outside listed service areas" if code is None else cities[code]["name"],
                             key=f"{prefix}-city-{state_code}{key_suffix}")
    if city_code is None:
        return state_code, None, None
    areas = cities[city_code]["areas"]
    area_codes = list(areas)
    display_areas = ([None] + area_codes) if allow_unknown else area_codes
    area_code = st.selectbox("Area / ward / locality", display_areas,
                             format_func=lambda code: "Outside listed service areas" if code is None else areas[code],
                             key=f"{prefix}-area-{city_code}{key_suffix}")
    return state_code, city_code, area_code
