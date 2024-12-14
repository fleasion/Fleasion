# CLI-1.01
# Fleasion-CLI, open sourced cache modifier, intended for Phantom Forces. plz dont abuse D:
# discord.gg/hXyhKehEZF

import os
import sys
import shutil
import time
import json
import platform
import tarfile
import requests

response = requests.get("https://raw.githubusercontent.com/fleasion/Fleasion/main/requirements.txt")
response.raise_for_status()
with open('requirements.txt', "w") as file:
    file.write(response.text)

import gdown


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
mesh_version = 'v2'


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

    stored_cache = 'cached_files'
    if not os.path.exists(stored_cache):
        url = 'https://drive.google.com/uc?id=127s3WozMOssKWrnWXCQMPBy83LZ-RTrR'
        gdown.download(url, 'downloaded_file.tar', quiet=False)

        with tarfile.open('downloaded_file.tar', 'r') as tar:
            tar.extractall()
        
        os.remove('downloaded_file.tar')
        
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
    run_version = run_lines[1][2:]
    if run_version == local_run_lines[1][2:]:
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

def dlist(area, specific_area=None):
    if specific_area:
        current_level = specific_area
    else:
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
    for idx, key in enumerate([k for k in presets.keys() if k != "DONTNAMETHIS"], start=1):
        print(f"{idx}: {GREEN}{key}{DEFAULT}")

    choice = input(": ")

    if choice.isdigit():
        choice = int(choice)
        valid_keys = [k for k in presets.keys() if k != "DONTNAMETHIS"]
        if 1 <= choice <= len(valid_keys):
            return valid_keys[choice - 1]
        else:
            print("Invalid number.")
            return None
    else:
        return choice

get_version()

cdir = os.path.dirname(os.path.abspath(__file__))
if os_name == "Windows":
    folder_path = os.path.join(os.getenv('TEMP'), 'roblox', 'http')
    temp_path = os.path.join(cdir, 'cached_files')
elif os_name == "Linux":
    folder_path = os.path.expanduser("~/.var/app/org.vinegarhq.Sober/cache/sober/http")
    temp_path = os.path.join(cdir, 'cached_files')
else:
    print(f"Unsupported OS - {os}")
    exit()

with open('assets.json', 'r') as file:
    data = json.load(file)

with open('presets.json', 'r') as file:
    presets = json.load(file)


def replace(files_to_delete, file_to_replace):
    try:
        copy_file_path = os.path.join(temp_path, file_to_replace)
        if os.path.exists(copy_file_path):
            for file_to_delete in files_to_delete:
                delete_file_path = os.path.join(folder_path, file_to_delete)
                if os.path.exists(delete_file_path):
                    os.remove(delete_file_path)
                else:
                    print(f'{RED}{file_to_delete} not found.{DEFAULT}')

                new_file_path = os.path.join(folder_path, file_to_delete)
                shutil.copy(copy_file_path, new_file_path)
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
        f"\nasset replacements:\n0:  {GREEN}Custom{DEFAULT}\n1:  {GREEN}Sights{DEFAULT}\n2:  {GREEN}Arm model tweaks{DEFAULT}\n3:  {GREEN}Sleeves{DEFAULT}\n4:  {GREEN}No textures{DEFAULT}\n5:  {GREEN}Default skyboxes{DEFAULT}\n6:  {GREEN}Gun skins{DEFAULT}\n7:  {GREEN}Gun Sounds{DEFAULT}\n8:  {GREEN}Gun smoke{DEFAULT}\n9:  {GREEN}Hit tweaks{DEFAULT}\n10: {GREEN}Grenade tweaks{DEFAULT}\n11: {GREEN}Misc tweaks{DEFAULT}\n12: {GREEN}Fizzy models{DEFAULT}")
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
                    f"\nEnter arm option:\n1: {GREEN}Remove options{DEFAULT}\n2: {GREEN}Bone arms{DEFAULT}\n: ")
                match int(arm_option):
                    case 1:
                        output.append(
                            (dlist('arm models', data['arm models'][mesh_version]), '5873cfba79134ecfec6658f559d8f320'))
                    case 2:
                        output.append(
                            (data["arm models"][mesh_version]["bare arms"], "5873cfba79134ecfec6658f559d8f320"))
                        if mesh_version == 'v1':
                            output.append((['f5b0bcba5570d196909a78c7a697467c', '7f828aee555e5e1161d4b39faddda970'],
                                           'c9672591983da8fffedb9cec7df1e521'))
                        if mesh_version == 'v2':
                            output.append((['2245ea538d66f8c9eb7f453aa3e421c2', 'd599df8997da0b6a3e5b12ab948f648b'],
                                           'c9672591983da8fffedb9cec7df1e521'))  # c9672591983da8fffedb9cec7df1e521 needs to be updated with new hash
                    case _:
                        print("Enter a Valid Option!")
            case 3:
                if os_name == "Linux":
                    output.append(([data["linux"]["defaults"]["sleeves"]], dlist("skins")))
                else:
                    output.append((['8813bbc8c0f7c0901fc38c1c85935fec'], dlist("skins"))) # aa33dd87fc9db92e891361e069da1849
            case 4:
                if os_name == "Linux":
                    output.append((data["linux"]["textures"], 'd625adff6a3d75081d11b3407b0b417c'))
                else:
                    output.append(
                        (data["textures"], 'd625adff6a3d75081d11b3407b0b417c'))  # no textures without downside
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
                    output.append(([dlist("gun skins", data['skins']['pf cases'])], dlist("skins")))
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
                    f"\nEnter misc option:\n1: {GREEN}M21 Garand Ping{DEFAULT}\n2: {GREEN}BFG Machina Sounds{DEFAULT}\n3: {GREEN}Damage Affect Tweaks{DEFAULT}\n4: {GREEN}Remove Flashlight Beam{DEFAULT}\n5: {GREEN}Remove Bullet Casing Sounds{DEFAULT}\n: ")
                match int(misc_option):
                    case 1:
                        output.append((["07fe5c19cdd350a4922412d00d567edd", "17bb7bd20bf6e1b41214619d16698ff4",
                                        "b36ed668aea77715747e3ebadce8a439", "fbc5302726777295ae2ccd092d2748f9"],
                                       "5873cfba79134ecfec6658f559d8f320"))
                        output.append((["877cb2de0924e058860135f72e800aad"], "9296d1de6b6a994aee0f95c1f5206b58"))
                    case 2:
                        output.append((["9d1808db108b86ddaeda18968a23a804"], "1689699496f4cf0e2f0fade63f68b83a"))
                        output.append((["3ad4ddcb4c77ab8bdfc83cf9c0cfafa9", "edf091bb925fa87900910e501da97018",
                                        "768131a75f0d2d95e6799a0a5acd67c6", "3d92b91e96ef916b6717a53ef3f3a442",
                                        "32e321c27457289889ac0d5fa72f7d97"], "5873cfba79134ecfec6658f559d8f320"))
                        output.append((["160883329152d9abc5434a1b0982ec7d"], "0d05028f1eaeb0b97ecd0c473b484371"))
                    case 3:
                        damage_option = input(
                            f"\nEnter option:\n1: {GREEN}Remove Damage Effect{DEFAULT}\n2: {GREEN}Anti Damage Affect{DEFAULT}\n: ")
                        match int(damage_option):
                            case 1:
                                output.append(
                                    (["a0542ee89ad3cc311bb3f7d23ef94fe4"], "5873cfba79134ecfec6658f559d8f320 "))
                            case 2:
                                output.append(
                                    (["a0542ee89ad3cc311bb3f7d23ef94fe4"], "614546fcea8e0411a1c94d669809a459"))
                    case 4:
                        output.append((["960b11e6e7d549c8b12044201025093f"], "ac59980bedb36f4b240633b08b532d08"))
                    case 5:
                        output.append((["7b11fe3312b0801492d3e0f8dce62043", "853395973e94bf11a1c9edb8110da786",
                                        "1a566c1fd2deac2677bfa26b357b5cf9", "134d345ef675a18d2c73cdbb5ca03394",
                                        "f191e4a1f7ff200c57229c8c65c2e763", "18957c939764efa83229b65a05ab3fa7"],
                                       "5873cfba79134ecfec6658f559d8f320"))
                    case _:
                        print("Enter a Valid Option!")
            case 12:
                fizzy_option = input(
                    f"\nEnter misc option:\n1:  {GREEN}G50 > USP MATCH{DEFAULT}\n2:  {GREEN}Potato Grip > Flashlight Launcher{DEFAULT}\n3:  {GREEN}SCAR-L > AR2{DEFAULT}\n4:  {GREEN}M870 > Gravity Gun{DEFAULT}\n5:  {GREEN}ZIP22 > Spray Bottle{DEFAULT}\n6:  {GREEN}NTW > Tau Cannon{DEFAULT}\n7:  {GREEN}ASP Baton > Stun Stick{DEFAULT}\n8:  {GREEN}Hardballer > Hyperlaser{DEFAULT}\n9:  {GREEN}Skeleton Grip Laser{DEFAULT}\n10: {GREEN}Flashlight Laser{DEFAULT}\n11: {GREEN}Hecate > Railgun{DEFAULT}\n: ")
                match int(fizzy_option):
                    case 1:
                        output.append(
                            (data['fizzy'][mesh_version]['usp']['remove'], "058e54ef5ad3fb914c34a6f446a36702"))
                        output.append(
                            (data['fizzy'][mesh_version]['usp']['replace'],
                             data['fizzy'][mesh_version]['usp']['replacement']))
                    case 2:
                        output.append(
                            (data['fizzy'][mesh_version]['flashlight launcher']['remove'],
                             "058e54ef5ad3fb914c34a6f446a36702"))
                        output.append((data['fizzy'][mesh_version]['flashlight launcher']['replace'],
                                       data['fizzy'][mesh_version]['flashlight launcher']['replacement']))
                    case 3:
                        output.append(
                            (data['fizzy'][mesh_version]['ar2']['remove'], "058e54ef5ad3fb914c34a6f446a36702"))
                        output.append(
                            (data['fizzy'][mesh_version]['ar2']['replace'],
                             data['fizzy'][mesh_version]['ar2']['replacement']))
                    case 4:
                        output.append(
                            (data['fizzy'][mesh_version]['gravity gun']['remove'], "058e54ef5ad3fb914c34a6f446a36702"))
                        output.append((data['fizzy'][mesh_version]['gravity gun']['replace'],
                                       data['fizzy'][mesh_version]['gravity gun']['replacement']))
                    case 5:
                        output.append(
                            (data['fizzy'][mesh_version]['spray bottle']['remove'], "058e54ef5ad3fb914c34a6f446a36702"))
                        output.append((data['fizzy'][mesh_version]['spray bottle']['replace'],
                                       data['fizzy'][mesh_version]['spray bottle']['replacement']))
                    case 6:
                        output.append(
                            (data['fizzy'][mesh_version]['tau cannon']['remove'], "058e54ef5ad3fb914c34a6f446a36702"))
                        output.append((data['fizzy'][mesh_version]['tau cannon']['replace'],
                                       data['fizzy'][mesh_version]['tau cannon']['replacement']))
                    case 7:
                        output.append((data['fizzy'][mesh_version]['stun stick']['replace'],
                                       data['fizzy'][mesh_version]['stun stick']['replacement']))
                    case 8:
                        output.append(
                            (data['fizzy'][mesh_version]['hyperlaser']['remove'], "058e54ef5ad3fb914c34a6f446a36702"))
                        output.append((data['fizzy'][mesh_version]['hyperlaser']['replace'],
                                       data['fizzy'][mesh_version]['hyperlaser']['replacement']))
                        variant_option = input(
                            f"\nEnter misc option:\n1: {GREEN}Colorable{DEFAULT}\n2: {GREEN}Blue/Purple{DEFAULT}\n3: {GREEN}Yellow/Green{DEFAULT}\n4: {GREEN}Pink/Orange{DEFAULT}\n: ")
                        match int(variant_option):
                            case 1:
                                output.append((data['fizzy'][mesh_version]['hyperlaser']['custom']['default'],
                                               data['fizzy'][mesh_version]['hyperlaser']['custom']['colorable']))
                            case 2:
                                output.append((data['fizzy'][mesh_version]['hyperlaser']['custom']['default'],
                                               data['fizzy'][mesh_version]['hyperlaser']['custom']['blue/purple']))
                            case 3:
                                output.append((data['fizzy'][mesh_version]['hyperlaser']['custom']['default'],
                                               data['fizzy'][mesh_version]['hyperlaser']['custom']['yellow/green']))
                            case 4:
                                output.append((data['fizzy'][mesh_version]['hyperlaser']['custom']['default'],
                                               data['fizzy'][mesh_version]['hyperlaser']['custom']['pink/orange']))
                    case 9:
                        variant_option = input(
                            f"\nEnter misc option:\n1: {GREEN}Skelaser White{DEFAULT}\n2: {GREEN}Skelaser Yellow{DEFAULT}\n3: {GREEN}Skelaser Teal{DEFAULT}\n4: {GREEN}Skelaser Pink{DEFAULT}\n5: {GREEN}Skelaser White Dark{DEFAULT}\n6: {GREEN}Skelaser Yellow Dark{DEFAULT}\n7: {GREEN}Skelaser Teal Dark{DEFAULT}\n8: {GREEN}Skelaser Pink Dark{DEFAULT}\n: ")
                        match int(variant_option):
                            case 1:
                                output.append((data['fizzy'][mesh_version]['skeleton grip laser']['custom']['default'],
                                               data['fizzy'][mesh_version]['skeleton grip laser']['custom']['white']))
                            case 2:
                                output.append((data['fizzy'][mesh_version]['skeleton grip laser']['custom']['default'],
                                               data['fizzy'][mesh_version]['skeleton grip laser']['custom']['yellow']))
                            case 3:
                                output.append((data['fizzy'][mesh_version]['skeleton grip laser']['custom']['default'],
                                               data['fizzy'][mesh_version]['skeleton grip laser']['custom']['teal']))
                            case 4:
                                output.append((data['fizzy'][mesh_version]['skeleton grip laser']['custom']['default'],
                                               data['fizzy'][mesh_version]['skeleton grip laser']['custom']['pink']))
                            case 5:
                                output.append((data['fizzy'][mesh_version]['skeleton grip laser']['custom']['default'],
                                               data['fizzy'][mesh_version]['skeleton grip laser']['custom'][
                                                   'white dark']))
                            case 6:
                                output.append((data['fizzy'][mesh_version]['skeleton grip laser']['custom']['default'],
                                               data['fizzy'][mesh_version]['skeleton grip laser']['custom'][
                                                   'yellow dark']))
                            case 7:
                                output.append((data['fizzy'][mesh_version]['skeleton grip laser']['custom']['default'],
                                               data['fizzy'][mesh_version]['skeleton grip laser']['custom'][
                                                   'teal dark']))
                            case 8:
                                output.append((data['fizzy'][mesh_version]['skeleton grip laser']['custom']['default'],
                                               data['fizzy'][mesh_version]['skeleton grip laser']['custom'][
                                                   'pink dark']))
                    case 10:
                        output.append(
                            (data['fizzy'][mesh_version]['flashlight laser']['remove'],
                             "058e54ef5ad3fb914c34a6f446a36702"))
                        if mesh_version == 'v1':
                            variant_option = input(
                                f"\nEnter misc option:\n1: {GREEN}Flashlight Laser White{DEFAULT}\n2: {GREEN}Flashlight Laser Yellow{DEFAULT}\n3: {GREEN}Flashlight Laser Teal{DEFAULT}\n4: {GREEN}Flashlight Laser Pink{DEFAULT}\n5: {GREEN}Flashlight Laser White Dark{DEFAULT}\n6: {GREEN}Flashlight Laser Yellow Dark{DEFAULT}\n7: {GREEN}Flashlight Laser Teal Dark{DEFAULT}\n8: {GREEN}Flashlight Laser Pink Dark{DEFAULT}\n: ")
                            match int(variant_option):
                                case 1:
                                    output.append((data['fizzy'][mesh_version]['flashlight laser']['custom']['default'],
                                                   data['fizzy'][mesh_version]['flashlight laser']['custom']['white']))
                                case 2:
                                    output.append((data['fizzy'][mesh_version]['flashlight laser']['custom']['default'],
                                                   data['fizzy'][mesh_version]['flashlight laser']['custom']['yellow']))
                                case 3:
                                    output.append((data['fizzy'][mesh_version]['flashlight laser']['custom']['default'],
                                                   data['fizzy'][mesh_version]['flashlight laser']['custom']['teal']))
                                case 4:
                                    output.append((data['fizzy'][mesh_version]['flashlight laser']['custom']['default'],
                                                   data['fizzy'][mesh_version]['flashlight laser']['custom']['pink']))
                                case 5:
                                    output.append((data['fizzy'][mesh_version]['flashlight laser']['custom']['default'],
                                                   data['fizzy'][mesh_version]['flashlight laser']['custom'][
                                                       'white dark']))
                                case 6:
                                    output.append((data['fizzy'][mesh_version]['flashlight laser']['custom']['default'],
                                                   data['fizzy'][mesh_version]['flashlight laser']['custom'][
                                                       'yellow dark']))
                                case 7:
                                    output.append((data['fizzy'][mesh_version]['flashlight laser']['custom']['default'],
                                                   data['fizzy'][mesh_version]['flashlight laser']['custom'][
                                                       'teal dark']))
                                case 8:
                                    output.append((data['fizzy'][mesh_version]['flashlight laser']['custom']['default'],
                                                   data['fizzy'][mesh_version]['flashlight laser']['custom'][
                                                       'pink dark']))
                        if mesh_version == 'v2':
                            variant_option = input(
                                f"\nEnter misc option:\n1: {GREEN}Flashlight Laser Yellow{DEFAULT}\n2: {GREEN}Flashlight Laser Pink{DEFAULT}\n3: {GREEN}Flashlight Laser Orange{DEFAULT}\n4: {GREEN}Flashlight Laser Blue{DEFAULT}\n5: {GREEN}Flashlight Laser Black{DEFAULT}\n: ")
                            match int(variant_option):
                                case 1:
                                    output.append((data['fizzy'][mesh_version]['flashlight laser']['custom']['default'],
                                                   data['fizzy'][mesh_version]['flashlight laser']['custom']['yellow']))
                                case 2:
                                    output.append((data['fizzy'][mesh_version]['flashlight laser']['custom']['default'],
                                                   data['fizzy'][mesh_version]['flashlight laser']['custom']['pink']))
                                case 3:
                                    output.append((data['fizzy'][mesh_version]['flashlight laser']['custom']['default'],
                                                   data['fizzy'][mesh_version]['flashlight laser']['custom']['orange']))
                                case 4:
                                    output.append((data['fizzy'][mesh_version]['flashlight laser']['custom']['default'],
                                                   data['fizzy'][mesh_version]['flashlight laser']['custom']['blue']))
                                case 5:
                                    output.append((data['fizzy'][mesh_version]['flashlight laser']['custom']['default'],
                                                   data['fizzy'][mesh_version]['flashlight laser']['custom']['black']))
                    case 11:
                        output.append(
                            (data['fizzy'][mesh_version]['railgun']['remove'], "058e54ef5ad3fb914c34a6f446a36702"))
                        output.append((data['fizzy'][mesh_version]['railgun']['replace'],
                                       data['fizzy'][mesh_version]['railgun']['replacement']))
                        variant_option = input(
                            f"\nEnter barrel option:\n1: {GREEN}Regular Barrel{DEFAULT}\n2: {GREEN}Long Barrel{DEFAULT}\n3: {GREEN}Short Barrel{DEFAULT}\n: ")
                        match int(variant_option):
                            case 1:
                                output.append((data['fizzy'][mesh_version]['railgun']['custom']['default'],
                                               data['fizzy'][mesh_version]['railgun']['custom']['regular']))
                            case 2:
                                output.append((data['fizzy'][mesh_version]['railgun']['custom']['default'],
                                               data['fizzy'][mesh_version]['railgun']['custom']['heavy']))
                            case 3:
                                output.append((data['fizzy'][mesh_version]['railgun']['custom']['default'],
                                               data['fizzy'][mesh_version]['railgun']['custom']['light']))
            case _:
                print("Invalid number.")
    except Exception as e:
        print(f"{RED}Error: {e}{DEFAULT}")

    return output


print(f"Welcome to: {GREEN}Fleasion-CLI!{DEFAULT}\n\nThis legacy version is going to be deprecated soon\nTo stay up to date and see live progress join\n: {GREEN}discord.gg/hXyhKehEZF{DEFAULT}\n")
start = True
while True:
    if not start:
        print(" ")
    start = False
    menu = input(
        f"Enter the number corresponding to what you'd like to do:\n1: {GREEN}Ingame asset replacements{DEFAULT}\n2: {GREEN}Presets{DEFAULT}\n3: {GREEN}Block (experimental, dont use){DEFAULT}\n4: {GREEN}Cache Settings{DEFAULT}\n5: {GREEN}Settings{DEFAULT}\n6: {GREEN}Changelog{DEFAULT}\n7: {GREEN}Exit{DEFAULT}\n: ")
    if menu == '1':
        replacements = get_hashes()
        for replacement in replacements:
            if isinstance(replacement[1], list):
                if len(replacement[0]) == len(replacement[1]):
                    for i, replac in enumerate(replacement[0]):
                        replace([replac], replacement[1][i])
            else:
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
                with open('presets.json', 'r') as file:
                    data = json.load(file)
                #presetpick = data.get('DONTNAMETHIS', None)
                presetpick = 'N/A'

                print(
                    f"\nSettings:\n1: {GREEN}Auto Cache Clear : {cache_color}{cacheclear}{DEFAULT}\n2: {GREEN}Apply preset on lauch: {DEFAULT}{presetpick}\n"
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
        print(f'\n{GREEN}Changelog:{DEFAULT}')
        print(data['changelog'])
        input()

    elif menu == '7':
        print("\nExiting the program.")
        break

    else:
        print("Invalid, type a corresponding number!")
