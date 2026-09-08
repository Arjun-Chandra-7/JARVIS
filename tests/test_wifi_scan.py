"""Wi-Fi scan parsing and the merge of nearby networks with our own saved passwords."""
from jarvis.integrations import wifi_scan as w


def test_terse_field_split_handles_escaped_colons():
    assert w._fields(r"My\:Net:75:WPA2:6") == ["My:Net", "75", "WPA2", "6"]
    assert w._fields(r"plain:80:open:1") == ["plain", "80", "open", "1"]


def test_parse_networks_keeps_best_signal_per_ssid():
    raw = "Home:75:WPA2:1\nHome:60:WPA2:36\nCafe:40:WPA2:6\n:88:WPA2:1\n"
    nets = w.parse_networks(raw)
    assert [n["ssid"] for n in nets] == ["Home", "Cafe"]     # hidden (empty) dropped
    assert nets[0]["signal"] == 75                            # best of the two Home rows
    assert nets[0]["security"] == "WPA2"


def test_parse_saved_names_only_wireless():
    raw = "Home:802-11-wireless\nEthernet 1:802-3-ethernet\nCafe:802-11-wireless\n"
    assert w.parse_saved_names(raw) == ["Home", "Cafe"]


def _fake_runner(scan_out, saved_names, psks):
    def run(args):
        if args[:4] == ["nmcli", "-t", "-f", "SSID,SIGNAL,SECURITY,CHAN"]:
            return scan_out
        if args[:4] == ["nmcli", "-t", "-f", "NAME,TYPE"]:
            return saved_names
        if "802-11-wireless-security.psk" in args:
            return psks.get(args[-1], "") + "\n"
        if "802-11-wireless.ssid" in args:
            return args[-1] + "\n"                             # connection name == ssid here
        return ""
    return run


def test_scan_attaches_saved_password_to_a_nearby_network():
    runner = _fake_runner(
        scan_out="Home:75:WPA2:1\nNeighbour:50:WPA2:6\n",
        saved_names="Home:802-11-wireless\n",
        psks={"Home": "hunter2secret"})
    nets = {n["ssid"]: n for n in w.scan(runner)}
    assert nets["Home"]["saved"] is True and nets["Home"]["password"] == "hunter2secret"
    # a network we have never joined shows no password — never a guess or a crack
    assert nets["Neighbour"]["saved"] is False and nets["Neighbour"]["password"] is None


def test_scan_lists_a_saved_network_out_of_range():
    runner = _fake_runner(
        scan_out="Home:75:WPA2:1\n",
        saved_names="Home:802-11-wireless\nOffice:802-11-wireless\n",
        psks={"Home": "homepass", "Office": "officepass"})
    by = {n["ssid"]: n for n in w.scan(runner)}
    assert by["Office"]["out_of_range"] is True and by["Office"]["password"] == "officepass"


def test_report_never_fabricates_a_password():
    runner = _fake_runner(
        scan_out="Home:75:WPA2:1\nStranger:40:WPA3:11\n",
        saved_names="Home:802-11-wireless\n",
        psks={"Home": "homepass"})
    text = w.report(runner)
    assert "homepass" in text
    assert "Stranger" in text and "not saved on this device" in text
    assert "not recoverable" in text                          # states the honest limit
