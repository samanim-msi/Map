import os
import re
import sys
import glob
import zipfile
import tempfile
import xml.etree.ElementTree as ET
import pandas as pd
import simplekml
from html2image import Html2Image

RUN = "Industrial"
COMBINED_KMZ_OUTPUT = f"map/{RUN}_Final_Map_Report.kmz"

INCOMING_ICON_URL = "icons/inbound.png"
OUTGOING_ICON_URL = "icons/outbound.png"
DELTA_ICON_URL = "icons/delta1.png"
MISSING_ICON_URL = "icons/cross1.png"


def natural_sort_key(s):
    """Sorts strings containing numbers in natural human order (e.g. Site1 before Site10)."""
    return [int(text) if text.isdigit() else text.lower() for text in re.split(r'(\d+)', str(s))]


def generate_legend_image():
    stderr_fd = sys.stderr.fileno()
    saved_stderr_fd = os.dup(stderr_fd)
    devnull_fd = os.open(os.devnull, os.O_WRONLY)
    os.dup2(devnull_fd, stderr_fd)
    os.close(devnull_fd)

    try:
        hti = Html2Image(
            custom_flags=[
                '--hide-scrollbars',
                '--allow-file-access-from-files',
                '--default-background-color=00000000',
                '--disable-gpu',
                '--headless'
            ]
        )

        html_code = """<!DOCTYPE html>
        <html>
        <head>
        <style>
          * { box-sizing: border-box; }
          html, body {
            background: transparent !important;
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
            font-weight: 700;
            width: fit-content;
            margin: 0;
            padding: 6px;
          }
          
          .legend-box {
            background: rgb(28 28 30 / 85%);
            backdrop-filter: blur(12px);
            border: 1px solid rgba(255, 255, 255, 0.18);
            box-shadow: 0 6px 24px 0 rgba(0, 0, 0, 0.4);
            border-radius: 16px;
            padding: 10px 12px;
          }

          .legend_title {
            text-align: center;
            font-size: 13px;
            letter-spacing: 5px;
            color: rgba(255, 255, 255, 0.95);
            margin: 0 0 8px 0;
            text-transform: uppercase;
          }
          
          .columns { display: flex; gap: 8px; }
          .column { display: flex; flex-direction: column; gap: 6px; width: 175px; }

          .header {
            background: rgba(255, 255, 255, 0.12);
            border: 1px solid rgba(255, 255, 255, 0.2);
            border-radius: 12px;
            color: #ffffff;
            text-align: center;
            font-size: 11px;
            line-height: 1.25;
            height: 36px;
            display: flex;
            align-items: center;
            justify-content: center;
            font-weight: 600;
          }

          .item {
            border-radius: 12px;
            padding: 6px 4px;
            text-align: center;
            font-size: 12px;
            color: #1a1a1a;
            box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.3);
          }

          .rssi-orange { background: radial-gradient(circle at center, #fde3b3 0%, #f8b23a 100%); border: 1.5px solid #ff9900; }
          .rssi-green, .delta-green { background: radial-gradient(circle at center, #92e59e 0%, #037e2e 100%); border: 1.5px solid #027c35; }
          .rssi-yellow, .delta-yellow { background: radial-gradient(circle at center, #ffffbe 0%, #f5d905 100%); border: 1.5px solid #ffd500; }
          .rssi-red, .delta-red { background: radial-gradient(circle at center, #ffcccc 0%, #e01c1c 100%); border: 1.5px solid #d60202; }
        </style>
        </head>
        <body>
          <div class="legend-box">
            <h3 class="legend_title">LEGEND</h3>
            <div class="columns">
              <div class="column">
                <div class="header">Inbound/Outbound<br>RSSI (dBm)</div>
                <div class="item rssi-orange">0 to -40.99</div>
                <div class="item rssi-green">-41 to -90.99</div>
                <div class="item rssi-yellow">-91 to -104.99</div>
                <div class="item rssi-red">-105+</div>
              </div>
              <div class="column">
                <div class="header">Imbalance Delta<br>(dBm)</div>
                <div class="item delta-green">0 to 5.99</div>
                <div class="item delta-yellow">6 to 9.99</div>
                <div class="item delta-red">10+</div>
              </div>
            </div>
          </div>
        </body>
        </html>"""

        hti.screenshot(html_str=html_code, save_as='legend.png', size=(390, 310))
    finally:
        os.dup2(saved_stderr_fd, stderr_fd)
        os.close(saved_stderr_fd)


def format_decimal(val, decimals=2):
    if pd.isna(val) or str(val).strip().upper() in ["", "N/A", "NONE", "-", "NAN"]:
        return "N/A"
    try:
        f_val = float(val)
        if decimals == 0:
            return f"{int(round(f_val))}"
        return f"{f_val:.{decimals}f}"
    except (ValueError, TypeError):
        return str(val)


def convert_to_decimal(coord_str):
    if pd.isna(coord_str):
        return None
    coord_str = str(coord_str).strip().upper()

    try:
        return float(coord_str)
    except ValueError:
        pass

    parts = re.findall(r"[\d\.]+", coord_str)
    if len(parts) >= 3:
        d, m, s = float(parts[0]), float(parts[1]), float(parts[2])
        val = d + (m / 60.0) + (s / 3600.0)
        return -val if ("S" in coord_str or "W" in coord_str) else val
    elif len(parts) == 1:
        val = float(parts[0])
        return -val if ("W" in coord_str or "S" in coord_str) else val

    return None


def extract_site_id(row):
    val = str(row.get("Device_Site_ID", "Unknown")).strip()
    if val and val.lower() not in ["nan", "none", "-", "n/a", "unknown"]:
        try:
            float_val = float(val)
            if float_val.is_integer():
                return str(int(float_val))
        except ValueError:
            pass
        return val
    return ""


def extract_run_label(filename):
    base = os.path.basename(filename)
    match = re.search(r"(\d{1,2})[-_](\d{1,2})", base)
    if match:
        m, d = match.group(1).zfill(2), match.group(2).zfill(2)
        return f"{m}{d}"
    return base.replace(".csv", "").replace("_final_report", "").replace("_missing_outbound_report", "").replace("_connect_plus", "")


def clean_col_name(name):
    s = str(name).encode("utf-8").decode("utf-8-sig")
    return re.sub(r'[\r\n\t"\']', '', s).strip()


def get_column_value(row, possible_names):
    for col in possible_names:
        for actual_col in row.index:
            cleaned_col = clean_col_name(actual_col)
            if cleaned_col.lower() == col.lower():
                val = row[actual_col]
                if not pd.isna(val) and str(val).strip().upper() not in ["", "N/A", "NONE", "-", "NAN"]:
                    return str(val).strip()
    return "N/A"


def get_color_for_rssi(rssi_val):
    if pd.isna(rssi_val) or str(rssi_val).strip().upper() in ["", "N/A", "NONE", "-"]:
        return "ff000000"
    try:
        rssi = float(rssi_val)
        if rssi >= -40.99:
            return "ff168dfd"
        elif rssi >= -90.99:
            return "ff59933a"
        elif rssi >= -104.99:
            return "ff0ee7ff"
        else:
            return "ff1c1cff"
    except (ValueError, TypeError):
        return "ff000000"


def get_color_for_delta(delta_val):
    if delta_val == "N/A" or pd.isna(delta_val):
        return "ff000000"
    try:
        gap = abs(float(delta_val))
        if gap <= 5:
            return "ff59933a"
        elif gap <= 10:
            return "ff0ee7ff"
        else:
            return "ff1c1cff"
    except (ValueError, TypeError):
        return "ff000000"


def build_popup_html(
    card_title,
    outbound_time="N/A",
    inbound_time="N/A",
    time_delta="N/A",
    msg_type="N/A",
    lat="N/A",
    lon="N/A",
    raw_alt="N/A",
    outbound_rssi="N/A",
    inbound_rssi="N/A",
    calculated_delta="N/A",
    repeater_id="N/A",
    device_site_id="N/A",
    radio_type="N/A",
    radio_model="N/A",
    band="N/A",
    power="N/A",
    antenna_type="N/A",
):
    alt_str = f"{format_decimal(raw_alt, 1)} ft" if format_decimal(raw_alt, 1) != "N/A" else "N/A"
    out_rssi_str = format_decimal(outbound_rssi, 2)
    in_rssi_str = format_decimal(inbound_rssi, 2)
    delta_rssi_str = format_decimal(calculated_delta, 2)
    time_delta_str = format_decimal(time_delta, 0)
    time_delta_formatted = f"{time_delta_str}s" if time_delta_str != "N/A" else "N/A"

    delta_row = ""
    if calculated_delta != "N/A":
        delta_row = f"""<tr>
          <td style="padding: 1px 0; color: #aaaaaa; text-align: left;">Calculated Delta:</td>
          <td style="padding: 1px 0; text-align: right; font-weight: bold;">{delta_rssi_str} dB</td>
        </tr>"""

    return f"""<div style="font-family: Arial, sans-serif; width: 100%; max-width: 250px; background-color: #0a0a0a; color: #ffffff; border-radius: 5px; text-align: center; margin-top: -8px; margin-left: -2px; box-sizing: border-box; padding: 0 2px;">
      <div style="border-bottom: 1px solid #444444; padding-bottom: 6px; margin-bottom: 8px; font-weight: bold; font-size: 13px; letter-spacing: 0.5px;">
        {card_title}
      </div>
      <table style="width: 100%; border-collapse: collapse; font-size: 11px; line-height: 1.5; color: #ffffff;">
        <tr><td style="padding: 1px 0; color: #aaaaaa; text-align: left;">Outbound Time:</td><td style="padding: 1px 0; text-align: right;">{outbound_time}</td></tr>
        <tr><td style="padding: 1px 0; color: #aaaaaa; text-align: left;">Inbound Time:</td><td style="padding: 1px 0; text-align: right;">{inbound_time}</td></tr>
        <tr><td style="padding: 1px 0; color: #aaaaaa; text-align: left;">Time Delta:</td><td style="padding: 1px 0; text-align: right;">{time_delta_formatted}</td></tr>
        <tr><td style="padding: 1px 0; color: #aaaaaa; text-align: left;">Type:</td><td style="padding: 1px 0; text-align: right;">{msg_type}</td></tr>
        <tr><td style="padding: 1px 0; color: #aaaaaa; text-align: left;">Outbound RSSI:</td><td style="padding: 1px 0; text-align: right;">{out_rssi_str} dBm</td></tr>
        <tr><td style="padding: 1px 0; color: #aaaaaa; text-align: left;">Inbound RSSI:</td><td style="padding: 1px 0; text-align: right;">{in_rssi_str} dBm</td></tr>
        {delta_row}
        <tr><td style="padding: 1px 0; color: #aaaaaa; text-align: left;">Latitude:</td><td style="padding: 1px 0; text-align: right;">{lat}</td></tr>
        <tr><td style="padding: 1px 0; color: #aaaaaa; text-align: left;">Longitude:</td><td style="padding: 1px 0; text-align: right;">{lon}</td></tr>
        <tr><td style="padding: 1px 0; color: #aaaaaa; text-align: left;">Altitude:</td><td style="padding: 1px 0; text-align: right;">{alt_str}</td></tr>
        <tr><td style="padding: 1px 0; color: #aaaaaa; text-align: left;">Repeater ID:</td><td style="padding: 1px 0; text-align: right;">{repeater_id}</td></tr>
        <tr><td style="padding: 1px 0; color: #aaaaaa; text-align: left;">Site ID:</td><td style="padding: 1px 0; text-align: right;">{device_site_id}</td></tr>
        <tr><td style="padding: 1px 0; color: #aaaaaa; text-align: left;">Radio Type:</td><td style="padding: 1px 0; text-align: right;">{radio_type}</td></tr>
        <tr><td style="padding: 1px 0; color: #aaaaaa; text-align: left;">Radio Model:</td><td style="padding: 1px 0; text-align: right;">{radio_model}</td></tr>
        <tr><td style="padding: 1px 0; color: #aaaaaa; text-align: left;">Band:</td><td style="padding: 1px 0; text-align: right;">{band}</td></tr>
        <tr><td style="padding: 1px 0; color: #aaaaaa; text-align: left;">Power:</td><td style="padding: 1px 0; text-align: right;">{power}</td></tr>
        <tr><td style="padding: 1px 0; color: #aaaaaa; text-align: left;">Antenna Type:</td><td style="padding: 1px 0; text-align: right;">{antenna_type}</td></tr>
      </table>
    </div>"""


def combine_multiple_reports():
    report_files = sorted(glob.glob("final-reports/*_final_report.csv"), key=natural_sort_key)
    if not report_files:
        print("Error: No '*_final_report.csv' files found in 'final-reports/' folder.")
        return

    output_dir = os.path.dirname(COMBINED_KMZ_OUTPUT)
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)

    generate_legend_image()
    kml = simplekml.Kml()

    internal_legend_img = kml.addfile("legend.png") if os.path.exists("legend.png") else "legend.png"
    internal_inbound_icon = kml.addfile(INCOMING_ICON_URL) if os.path.exists(INCOMING_ICON_URL) else INCOMING_ICON_URL
    internal_outbound_icon = kml.addfile(OUTGOING_ICON_URL) if os.path.exists(OUTGOING_ICON_URL) else OUTGOING_ICON_URL
    internal_delta_icon = kml.addfile(DELTA_ICON_URL) if os.path.exists(DELTA_ICON_URL) else DELTA_ICON_URL
    internal_missing_icon = kml.addfile(MISSING_ICON_URL) if os.path.exists(MISSING_ICON_URL) else MISSING_ICON_URL

    screen_overlay = kml.newscreenoverlay(name="Legend")
    screen_overlay.icon.href = internal_legend_img
    screen_overlay.overlayxy = simplekml.OverlayXY(x=0, y=1, xunits=simplekml.Units.fraction, yunits=simplekml.Units.fraction)
    screen_overlay.screenxy = simplekml.ScreenXY(x=0.02, y=0.98, xunits=simplekml.Units.fraction, yunits=simplekml.Units.fraction)
    screen_overlay.rotationxy = simplekml.RotationXY(x=0, y=0, xunits=simplekml.Units.fraction, yunits=simplekml.Units.fraction)
    screen_overlay.size = simplekml.Size(x=310, y=260, xunits=simplekml.Units.pixels, yunits=simplekml.Units.pixels)
    screen_overlay.visibility = 1

    site_locations_folder = kml.newfolder(name="Site Locations")
    site_locations_folder.visibility = 0

    call_unsuccessful_root = kml.newfolder(name="CallUnsuccessful")
    call_unsuccessful_root.visibility = 0

    inbound_root = kml.newfolder(name="InboundRSSI")
    inbound_root.visibility = 0

    outbound_root = kml.newfolder(name="OutboundRSSI")
    outbound_root.visibility = 0

    delta_root = kml.newfolder(name="ImbalanceDelta")
    delta_root.visibility = 0

    connect_plus_root = kml.newfolder(name="Connect Plus")
    connect_plus_root.visibility = 0

    site_subfolders = {}
    run_folders = {"Inbound": {}, "Outbound": {}, "Delta": {}, "CallUnsuccessful": {}}

    for file_path in report_files:
        run_tag = extract_run_label(file_path)
        df = pd.read_csv(file_path, encoding="utf-8-sig")

        unsuccessful_folder_name = f"{run_tag}_Mobile_CallUnsuccessful.csv"
        if unsuccessful_folder_name not in run_folders["CallUnsuccessful"]:
            f_obj = call_unsuccessful_root.newfolder(name=unsuccessful_folder_name)
            f_obj.visibility = 0
            run_folders["CallUnsuccessful"][unsuccessful_folder_name] = f_obj

        for idx, row in df.iterrows():
            lat = convert_to_decimal(row.get("Latitude"))
            lon = convert_to_decimal(row.get("Longitude"))

            if pd.isna(lat) or pd.isna(lon) or (lat == 0 and lon == 0):
                continue

            if lon > 0:
                lon = -lon

            site_id = extract_site_id(row)
            site_key = f"Site {site_id}" if site_id and not site_id.lower().startswith("site") else (site_id or "Site Unknown")
            site_num = site_key.replace("Site ", "")

            outbound_rssi = row.get("Outbound_RSSI", "N/A")
            inbound_rssi = row.get("Inbound_RSSI", "N/A")

            try:
                delta_val = float(outbound_rssi) - float(inbound_rssi)
                delta_str = f"{round(delta_val, 1)}"
            except (ValueError, TypeError):
                delta_val = "N/A"
                delta_str = "N/A"

            row_kwargs = {
                "outbound_time": row.get("Outbound_Time", "N/A"),
                "inbound_time": row.get("Inbound_Time", "N/A"),
                "time_delta": row.get("Time_Delta_Sec", "N/A"),
                "msg_type": row.get("Type", "N/A"),
                "lat": row.get("Latitude", "N/A"),
                "lon": row.get("Longitude", "N/A"),
                "raw_alt": row.get("Alt. (ft)", "N/A"),
                "outbound_rssi": outbound_rssi,
                "inbound_rssi": inbound_rssi,
                "repeater_id": row.get("Repeater_ID", "N/A"),
                "device_site_id": row.get("Device_Site_ID", "N/A"),
                "radio_type": row.get("Radio_Type", "N/A"),
                "radio_model": row.get("Radio_Model", "N/A"),
                "band": row.get("Band", "N/A"),
                "power": row.get("Power", "N/A"),
                "antenna_type": row.get("Antenna_Type", "N/A"),
            }

            if site_key not in site_subfolders:
                s_in = inbound_root.newfolder(name=site_key)
                s_in.visibility = 0

                s_out = outbound_root.newfolder(name=site_key)
                s_out.visibility = 0

                s_del = delta_root.newfolder(name=site_key)
                s_del.visibility = 0

                site_subfolders[site_key] = {"Inbound": s_in, "Outbound": s_out, "Delta": s_del}

            in_run_key = f"{site_key}_{run_tag}_Inbound"
            if in_run_key not in run_folders["Inbound"]:
                f_name = f"{run_tag}_Site{site_num}_Mobile_InboundRSSI.csv"
                sub = site_subfolders[site_key]["Inbound"].newfolder(name=f_name)
                sub.visibility = 0
                run_folders["Inbound"][in_run_key] = sub

            out_run_key = f"{site_key}_{run_tag}_Outbound"
            if out_run_key not in run_folders["Outbound"]:
                f_name = f"{run_tag}_Site{site_num}_Mobile_OutboundRSSI.csv"
                sub = site_subfolders[site_key]["Outbound"].newfolder(name=f_name)
                sub.visibility = 0
                run_folders["Outbound"][out_run_key] = sub

            del_run_key = f"{site_key}_{run_tag}_Delta"
            if del_run_key not in run_folders["Delta"]:
                f_name = f"{run_tag}_Site{site_num}_Mobile_ImbalanceDelta.csv"
                sub = site_subfolders[site_key]["Delta"].newfolder(name=f_name)
                sub.visibility = 0
                run_folders["Delta"][del_run_key] = sub

            p_in = run_folders["Inbound"][in_run_key].newpoint(name=site_id, coords=[(lon, lat)])
            p_in.visibility = 0
            p_in.description = build_popup_html(card_title="Inbound", **row_kwargs)
            p_in.style.iconstyle.color = get_color_for_rssi(inbound_rssi)
            p_in.style.iconstyle.icon.href = internal_inbound_icon
            p_in.style.iconstyle.scale = 0.6
            p_in.style.labelstyle.color = "ffffffff"
            p_in.style.labelstyle.scale = 0.65
            p_in.style.balloonstyle.bgcolor = "ff0a0a0a"
            p_in.style.balloonstyle.textcolor = "ffffffff"
            p_in.style.balloonstyle.text = "$[description]"

            p_out = run_folders["Outbound"][out_run_key].newpoint(name=site_id, coords=[(lon, lat)])
            p_out.visibility = 0
            p_out.description = build_popup_html(card_title="Outbound", **row_kwargs)
            p_out.style.iconstyle.color = get_color_for_rssi(outbound_rssi)
            p_out.style.iconstyle.icon.href = internal_outbound_icon
            p_out.style.iconstyle.scale = 0.6
            p_out.style.labelstyle.color = "ffffffff"
            p_out.style.labelstyle.scale = 0.65
            p_out.style.balloonstyle.bgcolor = "ff0a0a0a"
            p_out.style.balloonstyle.textcolor = "ffffffff"
            p_out.style.balloonstyle.text = "$[description]"

            p_del = run_folders["Delta"][del_run_key].newpoint(name=site_id, coords=[(lon, lat)])
            p_del.visibility = 0
            p_del.description = build_popup_html(card_title="Delta RSSI", calculated_delta=delta_str, **row_kwargs)
            p_del.style.iconstyle.color = get_color_for_delta(delta_val)
            p_del.style.iconstyle.icon.href = internal_delta_icon
            p_del.style.iconstyle.scale = 0.6
            p_del.style.labelstyle.color = "ffffffff"
            p_del.style.labelstyle.scale = 0.65
            p_del.style.balloonstyle.bgcolor = "ff0a0a0a"
            p_del.style.balloonstyle.textcolor = "ffffffff"
            p_del.style.balloonstyle.text = "$[description]"

    missing_files = sorted(glob.glob("final-reports/*_missing_outbound_report.csv") + glob.glob("*_missing_outbound_report.csv"), key=natural_sort_key)
    for file_path in missing_files:
        run_tag = extract_run_label(file_path)
        m_df = pd.read_csv(file_path, encoding="utf-8-sig").dropna(how="all")

        unsuccessful_folder_name = f"{run_tag}_Mobile_CallUnsuccessful.csv"
        if unsuccessful_folder_name not in run_folders["CallUnsuccessful"]:
            f_obj = call_unsuccessful_root.newfolder(name=unsuccessful_folder_name)
            f_obj.visibility = 0
            run_folders["CallUnsuccessful"][unsuccessful_folder_name] = f_obj

        target_subfolder = run_folders["CallUnsuccessful"][unsuccessful_folder_name]

        for idx, row in m_df.iterrows():
            lat = convert_to_decimal(row.get("Latitude"))
            lon = convert_to_decimal(row.get("Longitude"))

            if pd.isna(lat) or pd.isna(lon) or (lat == 0 and lon == 0):
                continue

            if lon > 0:
                lon = -lon

            outbound_rssi = get_column_value(row, ["RSSI (dBm)", "RSSI", "Outbound_RSSI", "Outbound RSSI"])

            p_miss = target_subfolder.newpoint(name="", coords=[(lon, lat)])
            p_miss.visibility = 0
            p_miss.description = build_popup_html(
                card_title="Call Unsuccessful",
                outbound_time=get_column_value(row, ["Time", "Outbound_Time", "Outbound Time", "Timestamp", "Date/Time", "Log_Time"]),
                msg_type=row.get("Type", "N/A"),
                lat=row.get("Latitude", "N/A"),
                lon=row.get("Longitude", "N/A"),
                raw_alt=row.get("Alt. (ft)", "N/A"),
                outbound_rssi=outbound_rssi,
            )
            p_miss.style.iconstyle.color = get_color_for_rssi(outbound_rssi)
            p_miss.style.iconstyle.icon.href = internal_missing_icon
            p_miss.style.iconstyle.scale = 0.6
            p_miss.style.balloonstyle.bgcolor = "ff0a0a0a"
            p_miss.style.balloonstyle.textcolor = "ffffffff"
            p_miss.style.balloonstyle.text = "$[description]"

    connect_plus_files = sorted(list(set(glob.glob("connectplus/*.csv") + glob.glob("connect-plus/*.csv") + glob.glob("*CPlus*.csv"))), key=natural_sort_key)
    cp_date_folders = {}

    for file_path in connect_plus_files:
        c_name = os.path.basename(file_path)

        match_date = re.search(r"(\d{1,2})[-_](\d{1,2})[-_](\d{4})", c_name)
        if match_date:
            m, d, y = match_date.group(1).zfill(2), match_date.group(2).zfill(2), match_date.group(3)
            date_folder_name = f"{m}{d}{y}"
            formatted_file_name = f"{m}{d}_ConnectPlus_OutboundRSSI.csv"
        else:
            date_folder_name = "ConnectPlus_Logs"
            formatted_file_name = c_name

        if date_folder_name not in cp_date_folders:
            d_obj = connect_plus_root.newfolder(name=date_folder_name)
            d_obj.visibility = 0
            cp_date_folders[date_folder_name] = d_obj

        c_subfolder = cp_date_folders[date_folder_name].newfolder(name=formatted_file_name)
        c_subfolder.visibility = 0

        try:
            c_df = pd.read_csv(file_path, encoding="utf-8-sig", sep=None, engine="python")
        except Exception:
            c_df = pd.read_csv(file_path, encoding="utf-8-sig", sep=";")

        c_df = c_df.dropna(how="all")
        c_df.columns = [clean_col_name(col) for col in c_df.columns]

        for idx, row in c_df.iterrows():
            lat = convert_to_decimal(row.get("Latitude"))
            lon = convert_to_decimal(row.get("Longitude"))

            if pd.isna(lat) or pd.isna(lon) or (lat == 0 and lon == 0):
                continue

            if lon > 0:
                lon = -lon

            outbound_time_val = get_column_value(row, ["Time", "Outbound_Time", "Outbound Time", "Timestamp", "Date/Time", "Log_Time", "Time_GPS", "GPSTime"])
            if outbound_time_val == "N/A" and len(row.index) > 0:
                first_col_val = str(row.iloc[0]).strip()
                if first_col_val and first_col_val.upper() not in ["NAN", "NONE", "N/A", "-"]:
                    outbound_time_val = first_col_val

            outbound_rssi_val = get_column_value(row, ["RSSI (dBm)", "RSSI", "Outbound_RSSI", "Outbound RSSI"])
            msg_type_val = get_column_value(row, ["Type", "Message_Type", "MsgType"])

            row_kwargs = {
                "outbound_time": outbound_time_val,
                "inbound_time": "N/A",
                "time_delta": "N/A",
                "msg_type": msg_type_val,
                "lat": get_column_value(row, ["Latitude", "Lat"]),
                "lon": get_column_value(row, ["Longitude", "Lon", "Long"]),
                "raw_alt": get_column_value(row, ["Alt. (ft)", "Alt (ft)", "Altitude", "Alt"]),
                "outbound_rssi": outbound_rssi_val,
                "inbound_rssi": "N/A",
                "repeater_id": get_column_value(row, ["Repeater_ID", "Repeater ID", "Repeater"]),
                "device_site_id": get_column_value(row, ["Device_Site_ID", "Device Site ID", "Site_ID", "Site ID"]),
                "radio_type": get_column_value(row, ["Radio_Type", "Radio Type"]),
                "radio_model": get_column_value(row, ["Radio_Model", "Radio Model"]),
                "band": get_column_value(row, ["Band"]),
                "power": get_column_value(row, ["Power"]),
                "antenna_type": get_column_value(row, ["Antenna_Type", "Antenna Type"]),
            }

            p_cp = c_subfolder.newpoint(name="", coords=[(lon, lat)])
            p_cp.visibility = 0
            p_cp.description = build_popup_html(card_title="Connect Plus", **row_kwargs)
            p_cp.style.iconstyle.color = get_color_for_rssi(outbound_rssi_val)
            p_cp.style.iconstyle.icon.href = internal_outbound_icon
            p_cp.style.iconstyle.scale = 0.6
            p_cp.style.balloonstyle.bgcolor = "ff0a0a0a"
            p_cp.style.balloonstyle.textcolor = "ffffffff"
            p_cp.style.balloonstyle.text = "$[description]"



            

    kml.savekmz(COMBINED_KMZ_OUTPUT)
# ------------------------------------------------------------------------
    # Post-processing: Merge all site-locations/*.kmz cleanly into doc.kml
    # ------------------------------------------------------------------------
    site_files = sorted(glob.glob("site-locations/*.kmz"), key=natural_sort_key)

    if site_files:
        with tempfile.TemporaryDirectory() as tmp_dir:
            gen_dir = os.path.join(tmp_dir, "gen")
            with zipfile.ZipFile(COMBINED_KMZ_OUTPUT, "r") as z:
                z.extractall(gen_dir)

            doc_kml_path = os.path.join(gen_dir, "doc.kml")
            ET.register_namespace("", "http://www.opengis.net/kml/2.2")
            tree = ET.parse(doc_kml_path)
            master_root = tree.getroot()
            ns = {"kml": "http://www.opengis.net/kml/2.2"}

            target_site_folder = master_root.find(".//kml:Folder[kml:name='Site Locations']", ns)

            if target_site_folder is not None:
                for site_kmz in site_files:
                    site_name = os.path.splitext(os.path.basename(site_kmz))[0]
                    site_extract_dir = os.path.join(tmp_dir, site_name)

                    with zipfile.ZipFile(site_kmz, "r") as z_site:
                        z_site.extractall(site_extract_dir)

                        # 1. Copy image assets into site-specific output folder
                        site_assets_rel_dir = os.path.join("files", site_name)
                        site_assets_abs_dir = os.path.join(gen_dir, site_assets_rel_dir)
                        os.makedirs(site_assets_abs_dir, exist_ok=True)

                        for member in z_site.namelist():
                            if not member.endswith(".kml"):
                                src = os.path.join(site_extract_dir, member)
                                dst = os.path.join(site_assets_abs_dir, os.path.basename(member))
                                with open(src, "rb") as fsrc, open(dst, "wb") as fdst:
                                    fdst.write(fsrc.read())

                        # 2. Parse site KML & fix asset <href> paths
                        site_kml = [f for f in z_site.namelist() if f.endswith(".kml")][0]
                        site_kml_path = os.path.join(site_extract_dir, site_kml)
                        site_tree = ET.parse(site_kml_path)
                        site_root = site_tree.getroot()

                        for href in site_root.findall(".//kml:href", ns):
                            if href.text and not href.text.startswith("http"):
                                asset_filename = os.path.basename(href.text)
                                href.text = f"files/{site_name}/{asset_filename}"


                        #    Banner remove 
                        for parent in site_root.iter():
                            to_remove = []
                            for child in list(parent):
                                name_node = child.find("kml:name", ns)
                                node_name = name_node.text.strip().lower() if (name_node is not None and name_node.text) else ""
                                
                                if node_name in ["logo", "legend"]:
                                    to_remove.append(child)
                            
                            for elem in to_remove:
                                parent.remove(elem)

                        # 2. Cleanup empty Overlay folder if Logo was its only child
                        for parent in site_root.iter():
                            to_remove = []
                            for child in list(parent):
                                tag_name = child.tag.split("}")[-1] if "}" in child.tag else child.tag
                                name_node = child.find("kml:name", ns)
                                node_name = name_node.text.strip().lower() if (name_node is not None and name_node.text) else ""

                                if tag_name == "Folder" and node_name == "overlay":
                                    non_name_children = [c for c in list(child) if not c.tag.endswith("name")]
                                    if len(non_name_children) == 0:
                                        to_remove.append(child)

                            for elem in to_remove:
                                parent.remove(elem)


                        # 3. Create site container and attach children
                        site_container = ET.Element("{http://www.opengis.net/kml/2.2}Folder")
                        s_name = ET.SubElement(site_container, "{http://www.opengis.net/kml/2.2}name")
                        s_name.text = site_name

                        top_elem = site_root.find("kml:Document", ns) or site_root.find("kml:Folder", ns) or site_root
                        for child in list(top_elem):
                            tag_name = child.tag.split("}")[-1] if "}" in child.tag else child.tag
                            if tag_name in ["name", "visibility", "open"]:
                                continue
                            site_container.append(child)

                        target_site_folder.append(site_container)

            tree.write(doc_kml_path, xml_declaration=True, encoding="utf-8")

            with zipfile.ZipFile(COMBINED_KMZ_OUTPUT, "w", zipfile.ZIP_DEFLATED) as z_out:
                for root_dir, _, files in os.walk(gen_dir):
                    for file in files:
                        full_path = os.path.join(root_dir, file)
                        rel_path = os.path.relpath(full_path, gen_dir)
                        z_out.write(full_path, rel_path)

    print(f"Success: Combined Master KMZ Report generated at {COMBINED_KMZ_OUTPUT}")


if __name__ == "__main__":
    combine_multiple_reports()