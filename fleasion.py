# v1.8.21
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

mod_cache = True
pf_cache = False

mod_cache_check_path = os.path.join(folder_path, '3dbc81ab51ae36ab1de45855c9bb2b15') # 29ec14d6f908cabca7fae131487d96d8, 016a313606e2f99a85bb1a91083206fc
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
                        output.append((['2245ea538d66f8c9eb7f453aa3e421c2', 'd599df8997da0b6a3e5b12ab948f648b'],
                                       'c9672591983da8fffedb9cec7df1e521'))  # c9672591983da8fffedb9cec7df1e521 needs to be updated with new hash
                    case 3:
                        delete_stuff(data["arm models"]["everything"])
                    case _:
                        print("Enter a Valid Option!")
            case 3:
                if os_name == "Linux":
                    output.append(([data["linux"]["defaults"]["sleeves"]], dlist("skins")))
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
                    f"\nEnter misc option:\n1: {GREEN}M21 Garand Ping{DEFAULT}\n2: {GREEN}BFG Machina Sounds{DEFAULT}\n3: {GREEN}Damage Affect Tweaks{DEFAULT}\n4: {GREEN}Remove Flashlight Beam{DEFAULT}\n5: {GREEN}Fizzy's Models{DEFAULT}\n: ")
                match int(misc_option):
                    case 1:
                        output.append((["07fe5c19cdd350a4922412d00d567edd", "17bb7bd20bf6e1b41214619d16698ff4", "b36ed668aea77715747e3ebadce8a439", "fbc5302726777295ae2ccd092d2748f9"], "5873cfba79134ecfec6658f559d8f320"))
                        output.append((["877cb2de0924e058860135f72e800aad"], "9296d1de6b6a994aee0f95c1f5206b58"))
                    case 2:
                        output.append((["9d1808db108b86ddaeda18968a23a804"], "1689699496f4cf0e2f0fade63f68b83a"))
                        output.append((["3ad4ddcb4c77ab8bdfc83cf9c0cfafa9", "edf091bb925fa87900910e501da97018", "768131a75f0d2d95e6799a0a5acd67c6", "3d92b91e96ef916b6717a53ef3f3a442", "32e321c27457289889ac0d5fa72f7d97"], "5873cfba79134ecfec6658f559d8f320"))
                        output.append((["160883329152d9abc5434a1b0982ec7d"], "0d05028f1eaeb0b97ecd0c473b484371"))
                    case 3:
                        damage_option = input(
                            f"\nEnter option:\n1: {GREEN}Remove Damage Effect{DEFAULT}\n2: {GREEN}Anti Damage Affect{DEFAULT}\n: ")
                        match int(damage_option):
                            case 1:
                                output.append((["a0542ee89ad3cc311bb3f7d23ef94fe4"], "5873cfba79134ecfec6658f559d8f320 "))
                            case 2:
                                output.append((["a0542ee89ad3cc311bb3f7d23ef94fe4"], "614546fcea8e0411a1c94d669809a459"))
                    case 4:
                        output.append((["960b11e6e7d549c8b12044201025093f "], "058e54ef5ad3fb914c34a6f446a36702"))
                    case 5:
                        fizzy_option = input(
                            f"\nEnter misc option:\n1:  {GREEN}G50 > USP MATCH{DEFAULT}\n2:  {GREEN}Potato Grip > Flashlight Launcher{DEFAULT}\n3:  {GREEN}SCAR-L > AR2{DEFAULT}\n4:  {GREEN}M870 > Gravity Gun{DEFAULT}\n5:  {GREEN}ZIP22 > Spray Bottle{DEFAULT}\n6:  {GREEN}NTW > Tau Cannon{DEFAULT}\n7:  {GREEN}ASP Baton > Stun Stick{DEFAULT}\n8:  {GREEN}Hardballer > Hyperlaser{DEFAULT}\n9:  {GREEN}Skeleton Grip Laser{DEFAULT}\n10: {GREEN}Flashlight Laser{DEFAULT}\n11: {GREEN}Hecate > Railgun{DEFAULT}\n: ")
                        match int(fizzy_option):
                            case 1:# usp
                                output.append((["3f00e33051288bffa1ac79b773b830fa", "4aec7e58da2e2becaf7c89b07c0e7f2d",
                                                "8d78017e9c36ae56fc89f892bba17d34", "9e9c258127dca3d284dddc5f936c09e5",
                                                "41420741eea2b3983b2beded8b2981ff", "a2f269e8d2c55f0cdde158431671a263",
                                                "a5f760e77f806ec6566015065b714261", "a5761bc5fa46b848e9f29ff796dee639",
                                                "ae04c2e9fcd4c671d5bdf3f459bb0a57", "b744c4060252f3dce52c44121b8b1cfa",
                                                "c1f8f6a19a221ad29b6083914d4e87a2", "dc70acca1b103352b74656e4f178e732",
                                                "7199cdc55e4ebe5d79ddbe80fa30abff", "4eab4fdb38b9e916e08a2b7923c833de"],
                                               "cbf0cf37278b713d69a1224d87764b4e")) # remove
                                output.append((["08cd26b8f4d9ce9e0246ff45897b4d1a"], "b2eee454c6784021549b23e9ba565b91")) # bolt
                                output.append((["4800cddb752e523b14f61aa7224f5b4d"], "f212626256eaf96426ac179bb2bc6989")) # frame
                                output.append((["67cac3bcd1c1459f08766ae47cdce777"], "8ef03950bd07ae5ae305c91cd651d780")) # mag
                                output.append((["29d21c6a319af85e851b10ee403beda6"], "cbf0cf37278b713d69a1224d87764b4e")) # sound
                            case 2: # grenade launcher
                                output.append((["9ad745fe192c66514499d082097c7969", "771a8d71308bcfb93af7a248e08a27ad",
                                                "960b11e6e7d549c8b12044201025093f", "db4a504a99eab17744a3ae7a018302c8"],
                                                "cbf0cf37278b713d69a1224d87764b4e")) # remove
                                output.append((["af9664cbb6a3830f94212ddbc25b2614"], "758e9dd654e8f31b7cf75f12faaec60c")) # potato
                            case 3: # ar2
                                output.append((["0bc8f39140a0ed5dcc3a184749497983", "4a140edf7ee3f64b158f111bfbd23154",
                                                 "4e9ec9484f5eb5f3ce1097a95cc19bb5", "79e7a1e7070822ee72a306d6fd42cb12",
                                                 "90f5503364604d6d86c23ad0ad19923f", "370ff8236b7456bd985d69a692dd8d3d",
                                                 "618dd900de44d24b976c71539e7fcbf8", "7269ca818c16352b4c8716687ae7a6f9",
                                                 "105267e688312ddfe3c6d9f4bda1fdd0", "415893cfc6a5a3e72e58a4887a38444e",
                                                 "534417e331b5ace39ce942d9985d0a08", "665251ee6dc57621dba36792fb9b633c",
                                                 "d4f1c0fcbb0b10d7657864f5561a537e"], "cbf0cf37278b713d69a1224d87764b4e")) # remove
                                output.append((["6ab1746849d42266dbdbc5dded1ae178"], "0225d605b591d1786cf624a0ece1474f")) # reciever
                                output.append((["4886f9319b426a67c51781608ff45bb7"], "51b13c298b701895a96dc6c1538c6200")) # mag
                                output.append((["36944683beb83ae89a2f643c2fc89c0a"], "37190ca5952db9fe7c0eae8d2c719423")) # sound
                            case 4: # gravity gun
                                output.append((["64f69091b866e02a95cd63c19c6e702f", "155eb145fe6a1cacb755bfb677b77ff8",
                                                "266f0876a067d4eaaa3c0816cf1c84db", "812ae82827cd0a680d4b32d5f9caafb1",
                                                "713765ee540fdeaed408890b7fa9fe55", "dfb5ae54c345b331ad4ba0572516c892"],
                                                "cbf0cf37278b713d69a1224d87764b4e")) # remove
                                output.append((["418e878c09489775b598eeff69167012"], "2eedfa5dd91367bf1c3e26d73dff4376")) # bolt
                                output.append((["8e48c1c8446432e97a912689fcd8881c"], "6e3ef32a93233c5d428cbed7a925025d")) # glow
                                output.append((["d680fdf7ede413dcd3356c7a2164095d"], "d7e0faf9673c57b894170a68513ec1e4")) # stock
                                output.append((["840a637bcf78d82ea1817980c7b4e034"], "3c91d1da83166b85175b843f70abfabd")) # sound
                            case 5: # spray bottle
                                output.append((["015994d41c68c743958355fc579055ee", "a2f269e8d2c55f0cdde158431671a263",
                                                "b8c043c9acf732234c301bad93955752", "d1e7985581bc5793809c36eca52f2047",
                                                "d5f2bfadc1608830ea5f618440261bb5", "e038b421ec7b7238683bffc3ce243572"],
                                                "cbf0cf37278b713d69a1224d87764b4e")) # remove
                                output.append((["348e3732062fafc55e0c5b62c54eed6f"], "0018e91fa2c9144285480245d476045f")) # bottle
                                output.append((["a4d45b530a3f0abea9eddc54f30298f2"], "87c55a91cfcd08689795a9458a290415")) # bottle rings
                            case 6: # tau cannon
                                output.append((["f7b22c7fb3c2819422c9cd9f476703e5", "7aa291082a3151ab79734459b3ba86f9",
                                                "7ffcca6ba69d52cfa071197f982ea720", "22cff8974a00c35ccb6705443ff47648",
                                                "45fd977e73428e6fa46690a7b569f7ac", "84cfa981d4fcea3b622143de97afaf93",
                                                "91a51183f2d8fc4b80a35b9084dae5bf", "516dad980b6314a6c4ef20bc86490baf",
                                                "23395e5987a27b2d7c9483803583fbfc", "312127313eb3d42585164d4b6da56325",
                                                "bc40ab191e605b5b415cf341b4a0c477", "bed3dd4d286f501b869a944a59954c12",
                                                "dcbf9f73ba646a9c0dcd5330163ab904", "de82a9be0bd0997280d1f6d624adba27",
                                                "e5f9de7f337a1e0edc91475564e68165", "e29e204d4133976dc14f119edee042e6",
                                                "ebfe8e8806de75804755ce39f2d503d9", "ecbacf7ca6ba9fadadae885dea8dd22d",
                                                "eccbf1d6ccab88a050873993d3412412"], "058e54ef5ad3fb914c34a6f446a36702"))
                                output.append((["5d4fc4a397521b1f502dc6b5e02ebe04"], "ef673c8c6dd91e4b3838ee04ce59e6d2")) # mag
                                output.append((["3fc23807190d4700757599c4418b62cc"], "0f8ad48af8f75c64438aa7d38419c7ab")) # part1 43d3a2cb1557fc51be6bcb0d329489d3
                                output.append((["43d3a2cb1557fc51be6bcb0d329489d3"], "ec94a4a602c83e551bbfcccdf3d0000d")) # part1 (2) 3fc23807190d4700757599c4418b62cc
                                output.append((["98c46e363b32e6c2be25f3a375951f66"], "36ba75eb9dd62725fcb25d36acbeb47b")) # part2
                                output.append((["cd5656b20061988925ce2e5faf274150"], "a488627070a3f389c4a6dddb85ef7430")) # sound
                            case 7: # stun stick
                                output.append((["0a80927d93180dd5a41cf776d351f2b0"], "bb1ab18ed6822cadd12bc7485c925265")) # 1
                                output.append((["22ed03bb2af84d4ddaad3bcc306a2900"], "49c011ad1cc299fc8ba83f1356a61858")) # 2
                            case 8: # hyperlaser
                                output.append((["2c7cc1d2eadbaee51d2b4b123e269b42", "26c675ccc0ea8b28ad9f85ebaafbe1a8",
                                                "8fffd6838407d7f4dc1efb02cb9ccb6d", "de12a644d7fd4653e475228e4dab0bb3",
                                                "39aa3db66a0eef06d5c73f849d966ce2", "54de49c3aff858496a8bcb58efd2783e",
                                                "877a9ed1757b48f0d455d18f74a04e56", "0008903fcc9679741bdd95e6d8252f21",
                                                "79241ea61c9bb223e859e9a89cb3c256", "7038395e929cb30254bc2254de22b22b",
                                                "a2e0d08ec56b4992a8062073d2ea4f74", "a5ccf730568459759cda8555c00b3604",
                                                "a22f3d07c7ac98aa9d0e055352394d96", "ab6b62d04145f8c6c43ed5afdbaa5cb4",
                                                "b7f88c69a58890f1379912c78cb11e84", "c3d955811260580a07d38542de411973",
                                                "c09e6e448208c8f889cfb8dac1af1c85", "ca4878171b6a937fb4c49b61a394ee7e",
                                                "cd942702f5a5d4ec695cfa4e0ac0ddea", "d7eb2bcb9cac8e3f00a23d5b6aa2d7b4"],
                                                "cbf0cf37278b713d69a1224d87764b4e")) # remove
                                output.append((["d19f07035f25d77bbe2e0a7aa9670a0b"], "8a081b4f86b31acdb2b437091852bb00")) # base
                                output.append((["9ebc44df0f589329aa8f376f93b232bc"], "dc91c288b0b881300391ddb0acded9c0")) # sound
                                variant_option = input(
                                    f"\nEnter misc option:\n1: {GREEN}Colorable{DEFAULT}\n2: {GREEN}Blue/Purple{DEFAULT}\n3: {GREEN}Yellow/Green{DEFAULT}\n4: {GREEN}Pink/Orange{DEFAULT}\n: ")
                                match int(variant_option):
                                    case 1:
                                        output.append((["4a5fad33b4e710bea55514ee22855f1c"], "20d66fca7a5d5338aad3ed21518b82a7"))
                                    case 2:
                                        output.append((["4a5fad33b4e710bea55514ee22855f1c"], "725aa3f6672e0a1d6b1166b5aaeb3a07"))
                                    case 3:
                                        output.append((["4a5fad33b4e710bea55514ee22855f1c"], "83927615cf74b04b0d49d484ae1642be"))
                                    case 4:
                                        output.append((["4a5fad33b4e710bea55514ee22855f1c"], "2645527bc93d40703a5dc62d7fccd1a6"))
                            case 9: # skelaser
                                variant_option = input(
                                    f"\nEnter misc option:\n1: {GREEN}Skelaser White{DEFAULT}\n2: {GREEN}Skelaser Yellow{DEFAULT}\n3: {GREEN}Skelaser Teal{DEFAULT}\n4: {GREEN}Skelaser Pink{DEFAULT}\n5: {GREEN}Skelaser White Dark{DEFAULT}\n6: {GREEN}Skelaser Yellow Dark{DEFAULT}\n7: {GREEN}Skelaser Teal Dark{DEFAULT}\n8: {GREEN}Skelaser Pink Dark{DEFAULT}\n: ")
                                match int(variant_option):
                                    case 1:
                                        output.append((["7e5ca08b80173590bc6c243803c364b2"], "ff11e1e6d15e3a0027a6466c3757492e"))
                                    case 2:
                                        output.append((["7e5ca08b80173590bc6c243803c364b2"], "4e45dc479574c1d8f56efab5d1dad1c0"))
                                    case 3:
                                        output.append((["7e5ca08b80173590bc6c243803c364b2"], "35f12a7c9767d6ff27cd93b86ae7f8a9"))
                                    case 4:
                                        output.append((["7e5ca08b80173590bc6c243803c364b2"], "c95d885dcb18887b863742ab31e07beb"))
                                    case 5:
                                        output.append((["7e5ca08b80173590bc6c243803c364b2"], "8b8809f54101d4b3e605f2846ec55bb8"))
                                    case 6:
                                        output.append((["7e5ca08b80173590bc6c243803c364b2"], "0c770dee4b2220a0170fcfbd38563708"))
                                    case 7:
                                        output.append((["7e5ca08b80173590bc6c243803c364b2"], "8723c87d95c36b505876bc0e6d5f0b9a"))
                                    case 8:
                                        output.append((["7e5ca08b80173590bc6c243803c364b2"], "0a0880164b1f8246306f686c620f8646"))
                            case 10: # flashlight laser
                                output.append((["bbb75d4abce8f70a47b398c123cdf74a", "960b11e6e7d549c8b12044201025093f"], "ac59980bedb36f4b240633b08b532d08"))
                                output.append((["0042c394f10fe47aea7ae73e2f9c1e34"], "89d3b69c9ecac557c02d38c94f8d0404"))
                                variant_option = input(
                                    f"\nEnter misc option:\n1: {GREEN}Flashlight Laser Yellow{DEFAULT}\n2: {GREEN}Flashlight Laser Pink{DEFAULT}\n3: {GREEN}Flashlight Laser Orange{DEFAULT}\n4: {GREEN}Flashlight Laser Blue{DEFAULT}\n5: {GREEN}Flashlight Laser Black{DEFAULT}\n: ")
                                match int(variant_option):
                                    case 1:
                                        output.append((["c145dd83b9597185498b2016e47b63bd"], "2de1f3817d64e4cf1fc795fe831a6ee0"))
                                    case 2:
                                        output.append((["c145dd83b9597185498b2016e47b63bd"], "9ed8b8ab4d44e0858b0b41b26301c856"))
                                    case 3:
                                        output.append((["c145dd83b9597185498b2016e47b63bd"], "e848b385c5c22cbe5b459f632594aeba"))
                                    case 4:
                                        output.append((["c145dd83b9597185498b2016e47b63bd"], "d1aa1593e65527cc15fa74391292a97e"))
                                    case 5:
                                        output.append((["c145dd83b9597185498b2016e47b63bd"], "87c39fab4e93362e08bf20363b9047b8"))
                            case 11: # railgun
                                output.append((["2cd3c942c8bd338d3423e46569d661e8", "6f78a52ba1ca6e565b5c6370f105fbe6",
                                                "08daf715288b6ac105b411ef9a69aec9", "9c923f6ea89adac0236b78f59bc7990e",
                                                "18be493aee6c5723a3b8a5b978cf8f5b", "23fd14be6f8aed0bbd2f3908ea28af0a",
                                                "45fd977e73428e6fa46690a7b569f7ac", "186a295afd823459da91b65a8f0d0882",
                                                "203fd2640e860d6e419bd149d071e3cf", "412d8d242c842c288a7a34355868142f",
                                                "f95f145472479670dd246f0e9b95dc17", "7ff908bb00a8e45334e964adbf83c432",
                                                "e4d0b9a6217d87ea88e55f2c166b6625"],"cbf0cf37278b713d69a1224d87764b4e")) # remove
                                output.append((["5099a4c2d1cb7558d084c3148250e5cd"], "52f25d13dfc45710017fef78165f34a8")) # sound
                                variant_option = input(
                                    f"\nEnter barrel option:\n1: {GREEN}Regular Barrel{DEFAULT}\n2: {GREEN}Long Barrel{DEFAULT}\n3: {GREEN}Short Barrel{DEFAULT}\n: ")
                                match int(variant_option):
                                    case 1:
                                        output.append((["01771db08b6584ed3e6f3a15b1441c9d"], "ae393a7250d928b6e0ce033214ec280b")) # reciever
                                        output.append((["ce1a43f672c4e3ab42a70197c05cabca"], "13f1ff5a948b5f83b4d709a0e79082b7")) # regular barrel
                                    case 2:
                                        output.append((["01771db08b6584ed3e6f3a15b1441c9d"], "c2eaf5366b0d3f9e840a4973dee4a224")) # reciever
                                        output.append((["ce1a43f672c4e3ab42a70197c05cabca"], "99e2d8fce4d8d0293e17f577552859b7")) # heavy barrel
                                    case 3:
                                        output.append((["01771db08b6584ed3e6f3a15b1441c9d"], "d1c16712773a1b8be9a377d8f7dac129")) # reciever
                                        output.append((["ce1a43f672c4e3ab42a70197c05cabca"], "c75332b54add2f21f1b8a8e4a35360d3")) # light barrel

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
