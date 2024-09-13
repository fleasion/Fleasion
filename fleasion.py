# v1.8.13
# Fleasion, open sourced cache modifier made by @cro.p, intended for Phantom Forces. plz dont abuse D:
# discord.gg/v9gXTuCz8B

import os
import sys
import shutil
import time
import json
import webbrowser
import requests
import platform

README_URL = 'https://raw.githubusercontent.com/CroppingFlea479/Fleasion/main/README.md'
FLEASION_URL = 'https://raw.githubusercontent.com/CroppingFlea479/Fleasion/main/fleasion.py'
ASSETS_URL = 'https://raw.githubusercontent.com/CroppingFlea479/Fleasion/main/assets.json'
RUN_URL = 'https://raw.githubusercontent.com/CroppingFlea479/Fleasion/main/run.bat'
RUNSH_URL = 'https://raw.githubusercontent.com/CroppingFlea479/Fleasion/main/run.sh'
README_FILE = 'README.md'
FLEASION_FILE = 'fleasion.py'
ASSETS_FILE = 'assets.json'
RUN_FILE = 'run.bat'
RUNSH_FILE = 'run.sh'
GREEN, RED, BLUE, DEFAULT = '\033[32m', '\033[31m', '\033[34m', '\033[0m'
os_name = platform.system()
clear_command = 'cls' if os_name == 'Windows' else 'clear'


def fetch_lines(url, num_lines=1):
    response = requests.get(url)
    lines = response.text.splitlines()
    return lines[:num_lines], lines


def read_lines(file_name, num_lines=1):
    try:
        with open(file_name, 'r') as file:
            return [file.readline().strip() for _ in range(num_lines)]
    except FileNotFoundError:
        return [''] * num_lines


def update_file(file_name, lines):
    with open(file_name, 'w') as file:
        file.write('\n'.join(lines))


def get_version():
    global presets_file
    readme_first_line, readme_lines = fetch_lines(README_URL)
    fleasion_first_line, fleasion_lines = fetch_lines(FLEASION_URL)
    run_lines, all_run_lines = fetch_lines(RUN_URL, 2)
    runsh_lines, all_runsh_lines = fetch_lines(RUNSH_URL, 2)

    print("Validating file versions...")

    local_readme_first_line = read_lines(README_FILE)[0]
    if readme_first_line[0] == local_readme_first_line:
        print(f"ReadMe   {GREEN}{readme_first_line[0]}{DEFAULT}")
    else:
        update_file(README_FILE, readme_lines)
        print(f"Updated README.md to {BLUE}{readme_first_line[0]}{DEFAULT}")

    local_fleasion_first_line = read_lines(FLEASION_FILE)[0]
    fleasion_display = fleasion_first_line[0][2:]
    if fleasion_first_line[0] == local_fleasion_first_line:
        print(f"Fleasion {GREEN}{fleasion_display}{DEFAULT}")
    else:
        update_file(FLEASION_FILE, fleasion_lines)
        print(f"Updated fleasion.py to {BLUE}{fleasion_display}{DEFAULT}")
        os.execv(sys.executable, ['python'] + sys.argv)

    response_assets = requests.get(ASSETS_URL)
    response_json = response_assets.json()

    try:
        with open(ASSETS_FILE, 'r') as file:
            local_assets = json.load(file)
    except FileNotFoundError:
        local_assets = {}

    if response_json.get('version') == local_assets.get('version'):
        print(f"Assets   {GREEN}{response_json['version']}{DEFAULT}")
    else:
        with open(ASSETS_FILE, 'w') as file:
            json.dump(response_json, file, indent=4)
        print(f"Updated assets.json to {BLUE}{response_json['version']}{DEFAULT}")

    local_run_lines = read_lines(RUN_FILE, 2)
    run_version = run_lines[1][4:]
    if run_version == local_run_lines[1][4:]:
        print(f"Run.bat  {GREEN}{run_version}{DEFAULT}")
    else:
        update_file(RUN_FILE, all_run_lines)
        print(f"Updated run.bat to {BLUE}{run_version}{DEFAULT}")

    local_runsh_lines = read_lines(RUNSH_FILE, 2)
    runsh_version = runsh_lines[1][2:]
    if runsh_version == local_runsh_lines[1][2:]:
        print(f"Run.sh   {GREEN}{runsh_version}{DEFAULT}")
    else:
        update_file(RUNSH_FILE, all_runsh_lines)
        print(f"Updated run.sh to  {BLUE}{runsh_version}{DEFAULT}")

    presets_file = 'presets.json'
    if not os.path.exists(presets_file):
        with open(presets_file, 'w') as file:
            json.dump({
                "replace oled": [
                    '0fd98b21b47dbd948988ec1c67696af8',
                    '5873cfba79134ecfec6658f559d8f320',
                    '009b0b998ae084f23e5c0d7b1f9431b3',
                    '577f6c95249ebea2926892c3f3e8c040'
                ]
            }, file, indent=4)
        print(f"Created {BLUE}{presets_file}{DEFAULT}")

    time.sleep(1)
    os.system(clear_command)


def dlist(area):
    current_level = data[area]
    path = [area]

    while isinstance(current_level, dict):
        match = {}
        print(f"\nAvailable keys in {GREEN}{' -> '.join(path)}{DEFAULT}:")
        for j, key in enumerate(current_level):
            match[str(j + 1)] = key
            print(f"{j + 1}: {' ' if j < 9 else ''}{GREEN}{key}{DEFAULT}")

        user_input = input(
            f"Enter the key(name or number) you want to use in {GREEN}{' -> '.join(path)}{DEFAULT}\n(nest in keys with a period, type 'back' to go back, or 'skip' to skip)\n: ").strip().lower()

        if user_input == 'back':
            if len(path) > 1:
                path.pop()
                current_level = data[path[0]]
                for key in path[1:]:
                    current_level = current_level[key]
            else:
                print("You are already at the top level. Cannot go back.")
            continue

        if user_input == 'skip':
            print("Skipping category.")
            return

        if user_input in match.keys():
            selected_keys = [match[user_input]]
        else:
            selected_keys = user_input.split('.')
            selected_keys = [key.strip() for key in selected_keys]

        valid = True
        temp_level = current_level
        for key in selected_keys:
            if key in temp_level:
                temp_level = temp_level[key]
            else:
                print(f"{RED}Key '{key}' does not exist in '{' -> '.join(path)}'. Please try again.{DEFAULT}")
                valid = False
                break

        if valid:
            for key in selected_keys:
                path.append(key)
                current_level = current_level[key]

    return current_level


def bloxstrap():
    base_path = os.path.join(os.getenv('LOCALAPPDATA'), 'Bloxstrap', 'Modifications')
    nested_folders = ["PlatformContent", "pc", "textures", "sky"]

    if not os.path.exists(base_path):
        print(f"{RED}bloxstrap not found{DEFAULT}")
    else:
        path = base_path
        for folder in nested_folders:
            path = os.path.join(path, folder)
            if not os.path.exists(path):
                os.makedirs(path)
                print(f"Created folder: {path}")
            else:
                print(f"Folder already exists: {path}")

        print("All folders created successfully! Import your skyboxes into the opened folder.")
        os.startfile(path)

        replace(data["skyboxes"], 'd625adff6a3d75081d11b3407b0b417c')


def delete_stuff(files_to_delete):
    for file_to_delete in files_to_delete:
        delete_file_path = os.path.join(folder_path, file_to_delete)
        if os.path.exists(delete_file_path):
            os.remove(delete_file_path)  #
            print(f'{file_to_delete} has been deleted.')
        else:
            print(f'{RED}{file_to_delete} not found.{DEFAULT}')


def preset_check():
    print("\nAvailable presets:")
    for idx, key in enumerate(presets.keys(), start=1):
        print(f"{idx}: {GREEN}{key}{DEFAULT}")

    choice = input(": ")

    if choice.isdigit():
        choice = int(choice)
        if 1 <= choice <= len(presets):
            return list(presets.keys())[choice - 1]
        else:
            print("Invalid number.")
            return None
    else:
        return choice


get_version()

if os_name == "Windows":
    folder_path = os.path.join(os.getenv('TEMP'), 'roblox', 'http')
elif os_name == "Linux":
    folder_path = os.path.expanduser("~/.var/app/org.vinegarhq.Sober/cache/sober/http")
else:
    print(f"Unsupported OS - {os}")
    exit()

mod_cache = False
pf_cache = False

mod_cache_check_path = os.path.join(folder_path, '29ec14d6f908cabca7fae131487d96d8')  # 016a313606e2f99a85bb1a91083206fc
pf_cache_check_path = os.path.join(folder_path, '7b8ca4a4ec7addd0f55179a86e49a5a1' if os_name == 'Linux' else '8a7090ac9b2e858f4aee9e19a0bfd562')

if os.path.exists(mod_cache_check_path):
    mod_cache = True
if os.path.exists(pf_cache_check_path):
    pf_cache = True

if not mod_cache or not pf_cache:
    print(f"{RED}Missing cache, join prompted {'experiences' if not mod_cache or not pf_cache else 'experience'}.{DEFAULT}")
if not mod_cache:
    webbrowser.open_new_tab("https://www.roblox.com/games/18504289170/texture-game")
if not pf_cache:
    webbrowser.open_new_tab("https://www.roblox.com/games/292439477/Phantom-Forces")

while not mod_cache or not pf_cache:
    if os.path.exists(mod_cache_check_path) and not mod_cache:
        print(f"{GREEN}Modding{DEFAULT} cache detected")
        mod_cache = True

    if os.path.exists(pf_cache_check_path) and not pf_cache:
        print(f"{GREEN}PF{DEFAULT} cache detected")
        pf_cache = True

    if mod_cache and pf_cache:
        time.sleep(1)
        os.system(clear_command)

with open('assets.json', 'r') as file:
    data = json.load(file)

with open('presets.json', 'r') as file:
    presets = json.load(file)


def replace(files_to_delete, file_to_replace):
    try:
        copy_file_path = os.path.join(folder_path, file_to_replace)
        if os.path.exists(copy_file_path):
            for file_to_delete in files_to_delete:
                delete_file_path = os.path.join(folder_path, file_to_delete)
                if os.path.exists(delete_file_path):
                    os.remove(delete_file_path)
                    # print(f'{file_to_delete} has been deleted.')
                else:
                    print(f'{RED}{file_to_delete} not found.{DEFAULT}')

                new_file_path = os.path.join(folder_path, file_to_delete)
                shutil.copy(copy_file_path, new_file_path)
                # print(f'{copy_file_path} has been copied to {new_file_path}.')
                print(f'{BLUE}{file_to_delete} has been replaced with {file_to_replace}.{DEFAULT}')
        else:
            print(f'{RED}{file_to_replace} not found.{DEFAULT}')

    except Exception as e:
        if hasattr(e, 'winerror') and e.winerror == 183:
            pass
        else:
            print(f'{RED}An error occurred: {e}{DEFAULT}\n')


def get_hashes():
    output = []
    print(
        f"\nasset replacements:\n0:  {GREEN}Custom{DEFAULT}\n1:  {GREEN}Sights{DEFAULT}\n2:  {GREEN}Arm model tweaks{DEFAULT}\n3:  {GREEN}Sleeves{DEFAULT}\n4:  {GREEN}No textures{DEFAULT}\n5:  {GREEN}Default skyboxes{DEFAULT}\n6:  {GREEN}Gun skins{DEFAULT}\n7:  {GREEN}Gun Sounds{DEFAULT}\n8:  {GREEN}Gun smoke{DEFAULT}\n9:  {GREEN}Hit tweaks{DEFAULT}\n10: {GREEN}Grenade tweaks{DEFAULT}\n11: {GREEN}Misc tweaks{DEFAULT}")
    options = input(": ")
    try:
        match int(options):
            case 0:
                output.append(([input("\nEnter asset to change: ")], input("Enter replacement: ")))
            case 1:
                sight_option = input(
                    f"\nEnter sight option:\n1: {GREEN}Reticle tweaks{DEFAULT}\n2: {GREEN}Sight model tweaks{DEFAULT}\n3: {GREEN}Ballistics tracker tweaks{DEFAULT}\n: ")
                try:
                    match int(sight_option):
                        case 1:
                            reticle = dlist("reticles")
                            reticle_replacement = dlist("reticle replacement")
                            if reticle and reticle_replacement:
                                output.append(([reticle], reticle_replacement))
                        case 2:
                            sightbackground = input(
                                f"\nEnter background tweak:\n1: {GREEN}clear coyote blue background{DEFAULT}\n2: {GREEN}clear reflex blue background{DEFAULT}\n3: {GREEN}clear okp-7 blue background{DEFAULT}\n4: {GREEN}clear delta black ring{DEFAULT}\n5: {GREEN}remove sniper black circle{DEFAULT}\n6: {GREEN}remove glass hack border{DEFAULT}\n: ")
                            match int(sightbackground):
                                case 1:
                                    output.append((
                                        ['3fc9141fc7c1167c575b9361a98f04c0'],
                                        '5873cfba79134ecfec6658f559d8f320'))  # clear coyote blue background
                                case 2:
                                    output.append((
                                        ['2eaae4fe3a9fce967af993d27ad68d52'],
                                        '5873cfba79134ecfec6658f559d8f320'))  # clear reflex blue background
                                case 3:
                                    output.append((
                                        ['2eaae4fe3a9fce967af993d27ad68d52'],
                                        '5873cfba79134ecfec6658f559d8f320'))  # clear okp-7  blue background
                                case 4:
                                    output.append((
                                        ['30c4d2bb30b6b8c9ac7cfeec5db25a85', '7d5652167ec33ed349e569a55a398705'],
                                        'd625adff6a3d75081d11b3407b0b417c'))  # delta black ring
                                case 5:
                                    output.append((
                                        ['a883a2373ad6931556dce946c50c3690 ', '5a2a41b0da7ec98bf25780bb3f5d071f '],
                                        'd625adff6a3d75081d11b3407b0b417c'))  # remove sniper junk
                                case 6:
                                    output.append((
                                        ['1764672fe43c9f1d129b3d51dc3c40ee'],
                                        'd625adff6a3d75081d11b3407b0b417c'))  # remove sniper junk
                                case _:
                                    print("Invalid option")
                        case 3:
                            output.append(([data["ballistics tracker"]["default"]], dlist("ballistics tracker")))
                        case _:
                            print("Invalid option")
                except Exception as e:
                    print(f"{RED}Error: {e}{DEFAULT}")
            case 2:
                arm_option = input(
                    f"\nEnter arm option:\n1: {GREEN}Remove options{DEFAULT}\n2: {GREEN}Bone arms{DEFAULT}\n3: {GREEN}Default arms{DEFAULT}\n: ")
                match int(arm_option):
                    case 1:
                        output.append((dlist('arm models'), '5873cfba79134ecfec6658f559d8f320'))
                    case 2:
                        output.append((data["arm models"]["bare arms"], "5873cfba79134ecfec6658f559d8f320"))
                        output.append((['f5b0bcba5570d196909a78c7a697467c', '7f828aee555e5e1161d4b39faddda970'],
                                       'c9672591983da8fffedb9cec7df1e521'))
                    case 3:
                        delete_stuff(data["arm models"]["everything"])
                    case _:
                        print("Enter a Valid Option!")
            case 3:
                if os_name == "Linux":
                    output.append((data["linux"]["defaults"]["sleeves"], dlist("skins")))
                else:
                    output.append((['aa33dd87fc9db92e891361e069da1849'], dlist("skins")))
            case 4:
                if os_name == "Linux":
                    output.append((data["linux"]["textures"], 'd625adff6a3d75081d11b3407b0b417c'))
                else:
                    output.append((data["textures"], 'd625adff6a3d75081d11b3407b0b417c'))  # no textures without downside
            case 5:
                sky_option = input(
                    f"\nIs Bloxstrap sky folder setup?\n1: {GREEN}yes{DEFAULT}\n2: {GREEN}no{DEFAULT}\n: ")
                match int(sky_option):
                    case 1:
                        output.append((data["skyboxes"], 'd625adff6a3d75081d11b3407b0b417c'))  # forced default skybox
                    case 2:
                        bloxstrap()
                    case _:
                        print("Enter a Valid Option!")
            case 6:
                if os_name == "Linux":
                    output.append(([dlist("skins")], dlist("skins")))
                else:
                    output.append(([dlist("gun skins")], dlist("skins")))
            case 7:
                sound = dlist("gun sounds")
                sound_replacement = dlist("replacement sounds")
                if sound and sound_replacement:
                    output.append(([sound], sound_replacement))
            case 8:
                output.append((['8194373fb18740071f5e885bab349252'], dlist("gun smoke")))
            case 9:  #
                hit_option = input(
                    f"\nEnter hit option:\n1: {GREEN}Hitmarkers{DEFAULT}\n2: {GREEN}Hit sounds{DEFAULT}\n3: {GREEN}Kill sounds{DEFAULT}\n: ")
                match int(hit_option):
                    case 1:
                        output.append((['097165b476243d2095ef0a256320b06a'], dlist("hitmarker")))  # hitmarkers
                    case 2:
                        output.append((['a177d2c00abd3e550b873d76c97ad960'], dlist("replacement sounds")))
                    case 3:
                        output.append(
                            (data["replacement sounds"]["kill sounds"]["default"], dlist("replacement sounds")))
                    case _:
                        print("Enter a Valid Option!")
            case 10:
                boom_option = input(
                    f"\nEnter grenade option:\n1: {GREEN}Model tweaks{DEFAULT}\n2: {GREEN}Explosion sound{DEFAULT}\n3: {GREEN}Grenade sound{DEFAULT} \n: ")
                match int(boom_option):
                    case 1:
                        model_option = input(
                            f"\nEnter Model option:\n1: {GREEN}RGD{DEFAULT}\n2: {GREEN}Bundle{DEFAULT}\n: ")
                        match int(model_option):
                            case 1:
                                output.append((data["grenades"]["rgd"]["junk"], "5873cfba79134ecfec6658f559d8f320"))
                                output.append(([data["grenades"]["rgd"]["main"]], dlist("grenades")))
                                output.append(([data["grenades"]["rgd"]["texture"]], dlist("grenades")))
                            case 2:
                                output.append((data["grenades"]["bundle"]["junk"], "5873cfba79134ecfec6658f559d8f320"))
                                output.append((data["grenades"]["bundle"]["main"], dlist("grenades")))
                                output.append((data["grenades"]["bundle"]["texture"], dlist("grenades")))
                            case _:
                                print("Enter a Valid Option!")
                    case 2:
                        output.append(
                            (data["replacement sounds"]["explosions"]["default"], dlist("replacement sounds")))
                    case 3:
                        output.append(([dlist("grenade sounds")], dlist("replacement sounds")))
                    case _:
                        print("Enter a Valid Option!")
            case 11:
                misc_option = input(
                    f"\nEnter misc option:\n1: {GREEN}M21 Garand Ping{DEFAULT}\n2: {GREEN}BFG Machina Sounds{DEFAULT}\n3: {GREEN}Anti Damage Affect{DEFAULT}\n4: {GREEN}Remove Flashlight Beam{DEFAULT}\n5: {GREEN}Fizzy's Models{DEFAULT}\n: ")
                match int(misc_option):
                    case 1:
                        output.append((["07fe5c19cdd350a4922412d00d567edd", "17bb7bd20bf6e1b41214619d16698ff4", "b36ed668aea77715747e3ebadce8a439", "fbc5302726777295ae2ccd092d2748f9"], "5873cfba79134ecfec6658f559d8f320"))
                        output.append((["877cb2de0924e058860135f72e800aad"], "9296d1de6b6a994aee0f95c1f5206b58"))
                    case 2:
                        output.append((["9d1808db108b86ddaeda18968a23a804"], "1689699496f4cf0e2f0fade63f68b83a"))
                        output.append((["3ad4ddcb4c77ab8bdfc83cf9c0cfafa9", "edf091bb925fa87900910e501da97018", "768131a75f0d2d95e6799a0a5acd67c6", "3d92b91e96ef916b6717a53ef3f3a442", "32e321c27457289889ac0d5fa72f7d97"], "5873cfba79134ecfec6658f559d8f320"))
                        output.append((["160883329152d9abc5434a1b0982ec7d"], "0d05028f1eaeb0b97ecd0c473b484371"))
                    case 3:
                        output.append((["a0542ee89ad3cc311bb3f7d23ef94fe4"], "614546fcea8e0411a1c94d669809a459"))
                    case 4:
                        output.append((["960b11e6e7d549c8b12044201025093f "], "058e54ef5ad3fb914c34a6f446a36702"))
                    case 5:
                        fizzy_option = input(
                            f"\nEnter misc option:\n1: {GREEN}G50 > USP MATCH{DEFAULT}\n2: {GREEN}Potato Grip > Flashlight Launcher{DEFAULT}\n3: {GREEN}SCAR-L > AR2{DEFAULT}\n4: {GREEN}M870 > Gravity Gun{DEFAULT}\n5: {GREEN}ZIP22 > Spray Bottle{DEFAULT}\n6: {GREEN}NTW > Tau Cannon{DEFAULT}\n7: {GREEN}ASP Baton > Stun Stick{DEFAULT}\n8: {GREEN}Hardballer > Hyperlaser{DEFAULT}\n9: {GREEN}Skeleton Grip Laser{DEFAULT}\n10: {GREEN}Flashlight Laser{DEFAULT}\n: ")
                        match int(fizzy_option):
                            case 1:
                                output.append((["bdd9569ccf5265e1bcba9cfdf31158b4", "cdb896f02cec15070658302308e932a6",
                                                "1e2e2de92f74d63670f37492036fb3c8", "4d18ba857b6fb937b724ffe12d4c1334",
                                                "5af556d14ce3becb61476b01831469ba", "012aa01d8daeb6911ffa7894540aeda0",
                                                "742acba73607468f2aabed2393a485a8", "803dcfcc5d67796131c24808ecc81f5f",
                                                "dd56a65175d19f74f80ef965b581f8fe", "dd88384e2ab892de5289087d483f342f",
                                                "f9cea0253df6177c8a4d9ade17aeb519", "bd24d07dd480698db934a646b411c91f"],
                                               "058e54ef5ad3fb914c34a6f446a36702"))
                                output.append((["bddc9f845043a1557c1f60598b74dec9"], "3a24d59b1e8f104c593603d9a08f1849"))
                                output.append((["581fb1e75493f5cd07c392c56cfb6913"], "01b511d3d6a49e919136a7620c1c2d75"))
                                output.append((["b95f48b67fc264b3e0bed3ea67def86c"], "ce8f927a0723aecdf885ad8d2062d65f"))
                                output.append((["29d21c6a319af85e851b10ee403beda6"], "cbf0cf37278b713d69a1224d87764b4e"))
                            case 2:
                                output.append((["771a8d71308bcfb93af7a248e08a27ad", "db4a504a99eab17744a3ae7a018302c8",
                                                "dd9165376b8856778ab93d26ed52790e"], "058e54ef5ad3fb914c34a6f446a36702"))
                                output.append((["cd2870bb0ec785064a86d3ce2f2ec2cc"], "ca7d634e856f90edf499da0c26133900"))
                            case 3:
                                output.append((["0bc8f39140a0ed5dcc3a184749497983", "4a140edf7ee3f64b158f111bfbd23154",
                                                 "4e9ec9484f5eb5f3ce1097a95cc19bb5", "618dd900de44d24b976c71539e7fcbf8",
                                                 "7489cccbe6ce147ccbcc4416ff169633", "105267e688312ddfe3c6d9f4bda1fdd0",
                                                 "534417e331b5ace39ce942d9985d0a08", "665251ee6dc57621dba36792fb9b633c",
                                                 "aa0ee79510e22fbd065137957572e64d", "b1d53f0b6ce3d9efde41f85b5e9c3be3",
                                                 "d4f1c0fcbb0b10d7657864f5561a537e"], "058e54ef5ad3fb914c34a6f446a36702"))
                                output.append((["d807565c66ffa1ef5c2bcb8df71c3316"], "d0ab035539878e8b7f8db4afbcf35c68"))
                                output.append((["1bd29c76c25b635d72913cf651070211"], "9c824a1771dadc30fc47357781827d0b"))
                                output.append((["36944683beb83ae89a2f643c2fc89c0a"], "37190ca5952db9fe7c0eae8d2c719423"))
                            case 4:
                                output.append((["4a6f27c3d1c02fec75a680fa4e38cbc4", "64f69091b866e02a95cd63c19c6e702f",
                                                "481db139e933fe0326cd430c7440ff7f", "638bb4c3c30dee35672f2868367ee986",
                                                "77930c7e6a5743577138c2cbee275207"], "058e54ef5ad3fb914c34a6f446a36702"))
                                output.append((["d60b4c8da51f667748666d3b654447ef"], "11f5cb8d23e68bb0b46249a2324ef2e3"))
                                output.append((["f2fff6168a25903e756ce8b46fd5f222"], "7a8cdc037786af2ae38bc857a6f689bc"))
                                output.append((["a6f57f8e44e3e36ff63507b3201a2369"], "691ab049bccc077f4b555732c8bc95cd"))
                                output.append((["840a637bcf78d82ea1817980c7b4e034"], "3c91d1da83166b85175b843f70abfabd"))
                            case 5:
                                output.append((["6f18fab6028f963fe3b6a63ffd56db9f", "25b1de3e08c6909b19323d1ec31fff77",
                                                "223f269a9fdee763c0b125c20cdc7919", "885cad81b3f28d0333067fa763783cfc",
                                                "d5f2bfadc1608830ea5f618440261bb5"], "058e54ef5ad3fb914c34a6f446a36702"))
                                output.append((["272147901810ade7bb3de69b6de16a41"], "4a125b503471ff8e3a1a67762c0f2271"))
                                output.append((["368c5e823b66669d3fa3702d5bb83405"], "2d422447505c871a2370c8f9d86abfcf"))
                            case 6:
                                output.append((["3cf53b6dc15596a5dbf8b3e412180d7a", "7ead80946bc6e1319cb3345e60f5380f",
                                                "51ac841d64d72a6fd7ca820f5400612c", "52a63ced811527c03386c04366d8c5c1",
                                                "076ee7fe43824b3d094c82d7f2d31dfa", "445cadb9fe251623c21dc0228c564558",
                                                "529149dabb358f45dc37f65fbe69fe05", "0555544b3a949bdfd6d678fe1ee1db11",
                                                "a0f18c32520357cc642f7370bffdbd9a", "a775b83d510837e6504d7702b9173745",
                                                "b879ca0a2be08a97e1025f424414b720", "bfce01d85c5b112edf874697e8361d33",
                                                "c65d35d2a5b08f0de1cd5719b2a832d4", "d04a47352d271f4af0f7f18dc1271ec3",
                                                "eac5a3cb5fb92e128152d356e8ac69c1", "f7b22c7fb3c2819422c9cd9f476703e5",
                                                "f617b5d8f7d6a6ccbf6810b3a74e2c5d", "fd1e15a13f6223d92333a0e3890d0e1d",
                                                "64b85c4c6cbb582731cddfa47837fca4"], "058e54ef5ad3fb914c34a6f446a36702"))
                                output.append((["1dfc54edfbac54afef3149068cb5bd03"], "855b6209128f87538780d72daf80d8f1"))
                                output.append((["ce02760297d953caa05ffdb7dbde7da7"], "631400c77a40f183add73cb09ac57634"))
                                output.append((["153f63fb7e56c72c1ff4d99f73820019"], "70e7686f15cc964445dd26a003da5f76"))
                                output.append((["61fd2d1c541098583c023d41ca3d1e2e"], "c88ed6c1af512dc5ceba8930909bb11b"))
                                output.append((["cd5656b20061988925ce2e5faf274150"], "a488627070a3f389c4a6dddb85ef7430"))
                            case 7:
                                output.append((["e0c825a5deb0871b8e12376dd4fa40ba"], "f6a75145709a83372de39deacfe9ce27"))
                                output.append((["02d75c76b65f1f837b2f8b1684c7b9c4"], "49c011ad1cc299fc8ba83f1356a61858"))
                            case 8:
                                output.append((["0a89192cee6d4750d5692c2bbd117514", "2c69a5a9cb47f5305bfe8e5221e06dad",
                                                "2e3a2ee20d4ce477800b6709341a367f", "5f16814ee52de8dac2d6ce5be62d67c6",
                                                "7c18935a9aa64adb36c8833e04325c6f", "9e877683e8b814ff1042ed85ac7f57d8",
                                                "9ec1d61969c60760ba4563231a10d20f", "99de3c8563576612eb2593f18e4fc885",
                                                "163febf1c4242e4f27accd72f306d6c6", "548df330e8cdb635e871bb8c14e8e0a3",
                                                "689e339d1004552834acde0c566b27f3", "1511ed3cefa2e2065ba2c71bb0246f02",
                                                "036330297ca114d10e33be1a511146aa", "a585fa17681937f2c050096ee15afa51",
                                                "a78182f462e53a1becd34fa35a6c3b60", "ae518b8a68134743b97335b671a9646e",
                                                "bdaf13233d32c85d82fdf3eb99f86198", "d223f617fa26c8823bd7b0cb1263dd5e",
                                                "e9aefc8d611afa7253d19d657e003e56", "f8f6d6280f9ebab38590ac6221e9cd97"],
                                               "058e54ef5ad3fb914c34a6f446a36702"))
                                output.append((["9db5a1d17c1898c8bbf4f9801fa399fb"], "116aa5d47b919e6dfca38b321c9b54ae"))
                                output.append((["9ebc44df0f589329aa8f376f93b232bc"], "dc91c288b0b881300391ddb0acded9c0"))
                                variant_option = input(
                                    f"\nEnter misc option:\n1: {GREEN}Colorable{DEFAULT}\n2: {GREEN}Blue/Purple{DEFAULT}\n3: {GREEN}Yellow/Green{DEFAULT}\n4: {GREEN}Pink/Orange{DEFAULT}\n: ")
                                match int(variant_option):
                                    case 1:
                                        output.append((["cfa7d012e8010e2034966d75b53b033f"], "612d2995b2621bbd74853e6586e5b143"))
                                    case 2:
                                        output.append((["cfa7d012e8010e2034966d75b53b033f"], "5cc8e95a61df22732ab90a53f94857dd"))
                                    case 3:
                                        output.append((["cfa7d012e8010e2034966d75b53b033f"], "f6fb6ba8ddf16b6fea9220b1c7dd22aa"))
                                    case 4:
                                        output.append((["cfa7d012e8010e2034966d75b53b033f"], "ef4260fb36c2811cdb8f6d461d592983"))
                            case 9:
                                variant_option = input(
                                    f"\nEnter misc option:\n1: {GREEN}Skelaser White{DEFAULT}\n2: {GREEN}Skelaser Yellow{DEFAULT}\n3: {GREEN}Skelaser Teal{DEFAULT}\n4: {GREEN}Skelaser Pink{DEFAULT}\n5: {GREEN}Skelaser White Dark{DEFAULT}\n6: {GREEN}Skelaser Yellow Dark{DEFAULT}\n7: {GREEN}Skelaser Teal Dark{DEFAULT}\n8: {GREEN}Skelaser Pink Dark{DEFAULT}\n: ")
                                match int(variant_option):
                                    case 1:
                                        output.append((["0f0786855a64aee40604f9d850eb3217"], "e86fb3178b8a39b54441df22b7de7d6a"))
                                    case 2:
                                        output.append((["0f0786855a64aee40604f9d850eb3217"], "c764c5996197b3b38fee61e2f1c7fb17"))
                                    case 3:
                                        output.append((["0f0786855a64aee40604f9d850eb3217"], "89e1b68c63daa025471b9c3ab21ed82c"))
                                    case 4:
                                        output.append((["0f0786855a64aee40604f9d850eb3217"], "98f3ab09e8e11e20ba2763030187864d"))
                                    case 5:
                                        output.append((["0f0786855a64aee40604f9d850eb3217"], "1abc7b88c409c3463f25fa3c3f87fb7d"))
                                    case 6:
                                        output.append((["0f0786855a64aee40604f9d850eb3217"], "83d7d89ef8108ff3079c702b8d6d98d8"))
                                    case 7:
                                        output.append((["0f0786855a64aee40604f9d850eb3217"], "e22aa038866003ea7b1cac7420ed0429"))
                                    case 8:
                                        output.append((["0f0786855a64aee40604f9d850eb3217"], "f619d133b6d9d164d36c8b3067108f0f"))
                            case 10:
                                output.append((["deeae172f9761e48eb196753c46f1fa3", "960b11e6e7d549c8b12044201025093f",
                                                "3b07e8eecef3c62771ab9759204061ad"], "058e54ef5ad3fb914c34a6f446a36702"))
                                variant_option = input(
                                    f"\nEnter misc option:\n1: {GREEN}Flashlight Laser White{DEFAULT}\n2: {GREEN}Flashlight Laser Yellow{DEFAULT}\n3: {GREEN}Flashlight Laser Teal{DEFAULT}\n4: {GREEN}Flashlight Laser Pink{DEFAULT}\n5: {GREEN}Flashlight Laser White Dark{DEFAULT}\n6: {GREEN}Flashlight Laser Yellow Dark{DEFAULT}\n7: {GREEN}Flashlight Laser Teal Dark{DEFAULT}\n8: {GREEN}Flashlight Laser Pink Dark{DEFAULT}\n: ")
                                match int(variant_option):
                                    case 1:
                                        output.append((["201d2aa5244aabcafde4f96b0df79aa2"], "5255b02d41f1d3883767f0ccd7c803f1"))
                                    case 2:
                                        output.append((["201d2aa5244aabcafde4f96b0df79aa2"], "9c439bc79a498811331a1a59f9a39b8b"))
                                    case 3:
                                        output.append((["201d2aa5244aabcafde4f96b0df79aa2"], "e0dfd3d84c8a9812daba5787baf8cbe2"))
                                    case 4:
                                        output.append((["201d2aa5244aabcafde4f96b0df79aa2"], "265756e09928b57378baacf44dcaa632"))
                                    case 5:
                                        output.append((["201d2aa5244aabcafde4f96b0df79aa2"], "6f43bf88d6a4c50d4e404ea0d7e2cb5d"))
                                    case 6:
                                        output.append((["201d2aa5244aabcafde4f96b0df79aa2"], "d99085575e00b7dd01287060e4543c09"))
                                    case 7:
                                        output.append((["201d2aa5244aabcafde4f96b0df79aa2"], "8d42b0e61b84663156799b4f5e4638a9"))
                                    case 8:
                                        output.append((["201d2aa5244aabcafde4f96b0df79aa2"], "6f0d64d9ab1919afa348719c42dbfb72"))

                    case _:
                        print("Enter a Valid Option!")
            case _:
                print("Invalid number.")
    except Exception as e:
        print(f"{RED}Error: {e}{DEFAULT}")

    return output


print(f"Welcome to: {GREEN}Fleasion!{DEFAULT}\n")
start = True
while True:
    if not start:
        print(" ")
    start = False
    menu = input(
        f"Enter the number corresponding to what you'd like to do:\n1: {GREEN}Ingame asset replacements{DEFAULT}\n2: {GREEN}Presets{DEFAULT}\n3: {GREEN}Block (experimental, dont use){DEFAULT}\n4: {GREEN}Cache Settings{DEFAULT}\n5: {GREEN}Settings{DEFAULT}\n6: {GREEN}Exit{DEFAULT}\n: ")
    if menu == '1':
        replacements = get_hashes()
        for replacement in replacements:
            replace(replacement[0], replacement[1])

    elif menu == '2':
        preset_option = input(
            f"\nPresets:\n1: {GREEN}Load preset{DEFAULT}\n2: {GREEN}Add preset{DEFAULT}\n3: {GREEN}Delete preset{DEFAULT}\n: ")

        if preset_option == '1':
            if presets:
                name = preset_check()

                n_asset = 0
                r_asset = 1
                loops = 1
                if name:
                    values = int((len(presets[name]) / 2) + 1)
                if name in presets:
                    while loops != values:
                        replace([presets[name][n_asset]], presets[name][r_asset])
                        n_asset += 2
                        r_asset += 2
                        loops += 1
                else:
                    print(f"{RED}{name}{DEFAULT} does not exist.")
            else:
                print("No presets available")

        elif preset_option == '2':
            new_preset = input("\nEnter preset name\n: ")
            if new_preset not in presets:
                presets[new_preset] = []
            while True:
                replacements = get_hashes()
                for replacement in replacements:
                    if isinstance(replacement[1], list):
                        if len(replacement[0]) == len(replacement[1]):
                            print(replacement[0])
                            for i, replac in enumerate(replacement[0]):
                                presets[new_preset].append(replac)
                                presets[new_preset].append(replacement[1][i])
                                print(f"{BLUE}Added successfully ({replac} -> {replacement[1][i]}){DEFAULT}")
                        else:
                            print(f"{RED}This replacement is not supported, changes not applied{DEFAULT}")
                            presets[new_preset] = []
                    else:
                        for replac in replacement[0]:
                            presets[new_preset].append(replac)
                            presets[new_preset].append(replacement[1])
                            print(f"{BLUE}Added successfully ({replac} -> {replacement[1]}){DEFAULT}")
                with open('presets.json', 'w') as f:
                    json.dump(presets, f, indent=4)
                    print(f"{BLUE}Preset saved{DEFAULT}")
                repeat = input("Continue editing preset? (y/n)\n: ").lower()
                if repeat == 'n':
                    break

        elif preset_option == '3':
            if presets:
                name = preset_check()

                if name in presets:
                    del presets[name]
                    with open("presets.json", 'w') as file:
                        json.dump(presets, file, indent=4)
                    print(f"{GREEN}{name}{DEFAULT} deleted successfully.")
                else:
                    print(f"{RED}{name}{DEFAULT} does not exist.")
            else:
                print("No presets available to delete.")

        else:
            print("Invalid option")

    elif menu == '3':
        blockwarn = input(
            f"\n{RED}Warning: This is highly experimental and volatile to causing errors, requiring run.bat to be ran as admin to use. Only continue if you are aware of what youre doing.\nType 'done' to proceed, anything else will cancel.\n{DEFAULT}")
        if blockwarn == "done":
            file_path = r"C:\Windows\System32\drivers\etc\hosts"
            with open(file_path, "r") as file:
                content = file.read()

            blockedlist = []
            unblockedlist = []

            for i in range(8):
                if f"#127.0.0.1 c{i}.rbxcdn.com" in content:
                    unblockedlist.append(f"c{i}")
                elif f"127.0.0.1 c{i}.rbxcdn.com" in content:
                    blockedlist.append(f"c{i}")

                if f"#127.0.0.1 t{i}.rbxcdn.com" in content:
                    unblockedlist.append(f"t{i}")
                elif f"127.0.0.1 t{i}.rbxcdn.com" in content:
                    blockedlist.append(f"t{i}")

            print("\nCurrently blocked:", " ".join(blockedlist))
            print("Currently unblocked:", " ".join(unblockedlist))


            def website_blocks():
                website_blocklist = []
                print("Enter c(num)/t(num) to block/unblock (type 'done' when finished)")
                while True:
                    website_name = input("Enter string: ")
                    if website_name.lower() == 'done':
                        break
                    website_blocklist.append(website_name)
                return website_blocklist


            website_block = website_blocks()

            try:
                modified_content = content
                for string_thing in website_block:
                    if f"#127.0.0.1 {string_thing}.rbxcdn.com" in content:
                        modified_content = modified_content.replace(f"#127.0.0.1 {string_thing}.rbxcdn.com",
                                                                    f"127.0.0.1 {string_thing}.rbxcdn.com")
                        print("Blocked!")
                    elif f"127.0.0.1 {string_thing}.rbxcdn.com" in content:
                        modified_content = modified_content.replace(f"127.0.0.1 {string_thing}.rbxcdn.com",
                                                                    f"#127.0.0.1 {string_thing}.rbxcdn.com")
                        print("Unblocked!")
                    else:
                        print("No text found, blocking it.")
                        modified_content += f"\n127.0.0.1 {string_thing}.rbxcdn.com"

            except Exception as e:
                print(f"An error occurred: {e}")

            try:
                with open(file_path, "w") as file:
                    file.write(modified_content)
            except Exception as e:
                print(f"{RED}An error occurred: {e}{DEFAULT}")
        else:
            pass

    elif menu == '4':
        menu = input(
            f"\nEnter the number corresponding to what you'd like to do:\n1: {GREEN}Revert replacement{DEFAULT}\n2: {GREEN}Clear full cache{DEFAULT}\n: ")
        if menu == '1':
            replacements = get_hashes()
            for replacement in replacements:
                delete_stuff(replacement[0])

        elif menu == '2':
            resetkwarn = input(
                f"\n{RED}Warning: This will fully reset all tweaks and anything loaded from any game.\nType 'done' to proceed, anything else will cancel.\n{DEFAULT}")
            if resetkwarn == "done":
                def delete_all_in_directory(directory):
                    try:
                        if os.path.exists(directory):
                            for filename in os.listdir(directory):
                                file_path = os.path.join(directory, filename)
                                try:
                                    if os.path.isfile(file_path) or os.path.islink(file_path):
                                        os.unlink(file_path)
                                    elif os.path.isdir(file_path):
                                        shutil.rmtree(file_path)
                                except Exception as e:
                                    print(f'Failed to delete {file_path}. Reason: {e}')
                        else:
                            print(f'{RED}The directory {directory} does not exist.{DEFAULT}')
                    except Exception as e:
                        print(f'{RED}Error: {e}{DEFAULT}')


                delete_all_in_directory(folder_path)
                print("Cleared cache, rejoin relevant experiences")

    elif menu == '5':
        b_path = os.path.join(os.getenv('LOCALAPPDATA'), 'Bloxstrap', 'Modifications')
        Cset = ["ClientSettings", "ClientAppSettings.json"]
        settings_file_path = os.path.join(b_path, *Cset)

        cache_flags = {
            "DFIntNumAssetsMaxToPreload": "9999999",
            "DFIntAssetPreloading": "9999999",
            "DFIntHttpCacheCleanMinFilesRequired": "9999999"
        }

        if not os.path.exists(b_path):
            print(f"{RED}Bloxstrap not found{DEFAULT}")
        else:
            if not os.path.exists(settings_file_path):
                print(f"{RED}Settings file not found: {settings_file_path}{DEFAULT}")
            else:
                with open(settings_file_path, 'r') as file:
                    settings_data = json.load(file)

                cacheclear = "False"
                for key, value in cache_flags.items():
                    if settings_data.get(key) != value:
                        cacheclear = "True"
                        break

                cache_color = RED if cacheclear == "False" else BLUE

                print(
                    f"\nSettings:\n1: {GREEN}Auto Cache Clear : {cache_color}{cacheclear}{DEFAULT}\n"
                )

                settings = input(": ")
                try:
                    match int(settings):
                        case 1:
                            if cacheclear == "False":
                                for key in cache_flags.keys():
                                    settings_data.pop(key, None)
                                cacheclear = "True"
                                val2 = "True"
                                val_color = BLUE
                            else:
                                settings_data.update(cache_flags)
                                cacheclear = "False"
                                val2 = "False"
                                val_color = RED
                            val = "Auto Cache Clear"
                        case _:
                            print("Invalid number.")

                    with open(settings_file_path, 'w') as file:
                        json.dump(settings_data, file, indent=4)

                    print(f"\n{GREEN}Successfully changed {BLUE}{val}{GREEN} to {val_color}{val2}{DEFAULT}!")

                except ValueError:
                    print(f"{RED}Invalid input. Please enter a number.{DEFAULT}")
                except Exception as e:
                    print(f"{RED}Error: {e}{DEFAULT}")

    elif menu == '6':
        print("\nExiting the program.")
        break

    else:
        print("Invalid, type a corresponding number!")
