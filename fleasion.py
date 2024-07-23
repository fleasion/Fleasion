# v1.10
# Fleasion, open sourced cache modifier made by @cro.p, intended for Phantom Forces. plz dont abuse D:
# discord.gg/v9gXTuCz8B

import os
import sys
import shutil
import time
import getpass
import json
import webbrowser
import requests

README_URL = 'https://raw.githubusercontent.com/CroppingFlea479/ignore/main/read%20me'
FLEASION_URL = 'https://raw.githubusercontent.com/CroppingFlea479/ignore/main/fleasion.py'
ASSETS_URL = 'https://raw.githubusercontent.com/CroppingFlea479/ignore/main/assets'
README_FILE = 'READ ME.txt'
FLEASION_FILE = 'fleasion.py'
ASSETS_FILE = 'assets.json'

def fetch_first_line(url):
    response = requests.get(url)
    lines = response.text.splitlines()
    return lines[0], lines

def read_first_line(file_name):
    try:
        with open(file_name, 'r') as file:
            return file.readline().strip()
    except FileNotFoundError:
        return ''

def update_file(file_name, lines):
    with open(file_name, 'w') as file:
        file.write('\n'.join(lines))

def get_version():
    readme_first_line, readme_lines = fetch_first_line(README_URL)
    fleasion_first_line, fleasion_lines = fetch_first_line(FLEASION_URL)

    local_readme_first_line = read_first_line(README_FILE)
    if readme_first_line == local_readme_first_line:
        print(f"R V{readme_first_line}")
    else:
        update_file(README_FILE, readme_lines)
        print(f"Updated READ ME.txt! to v{readme_first_line}")

    local_fleasion_first_line = read_first_line(FLEASION_FILE)
    fleasiondisplay = fleasion_first_line[2:]
    if fleasion_first_line == local_fleasion_first_line:
        print(f"F {fleasiondisplay}")
    else:
        update_file(FLEASION_FILE, fleasion_lines)
        print(f"Updated fleasion.py to {fleasiondisplay}")
        os.execv(sys.executable, ['python'] + sys.argv)

    response_assets = requests.get(ASSETS_URL)
    response_json = response_assets.json()

    try:
        with open(ASSETS_FILE, 'r') as file:
            local_assets = json.load(file)
    except FileNotFoundError:
        local_assets = {}

    if response_json.get('version') == local_assets.get('version'):
        print(f"A {response_json['version']}")
    else:
        with open(ASSETS_FILE, 'w') as file:
            json.dump(response_json, file, indent=4)
        print(f"Updated assets.json to ({response_json['version']})")

config_file = 'assets.json'

with open('assets.json', 'r') as file:
    data = json.load(file)

def save_data_to_file(data, filename='assets.json'):
    with open(filename, 'w') as file:
        json.dump(data, file, indent=4)
    print(f"Data saved to {filename}")

def list_and_get_input(area):
    print(f"Available keys in '{area}':")
    for key in data[area]:
        print(f"{key}")
    
    input_file = input(f"Enter the key you want to use in '{area}': ")
    
    if input_file in data[area]:
        return data[area][input_file]
    else:
        new_key = input(f"'{input_file}' does not exist. Enter a new key name to add (leave blank to skip): ")
        if new_key:
            data[area][new_key] = input_file
            print(f"Added new key '{new_key}' with value '{input_file}' to '{area}'.")
            save_data_to_file(data)
            return input_file
        else:
            print("No new key added.")
            return input_file
    
def add_new_area():
    new_area = input("Enter the new area name: ")
    new_key = input(f"Enter the key for '{new_area}': ")
    new_value = input(f"Enter the value for '{new_key}': ")

    if new_area not in data:
        data[new_area] = {}

    data[new_area][new_key] = new_value

    # Write updated data back to JSON file
    with open(config_file, 'w') as file:
        json.dump(data, file, indent=4)

    print(f"New area '{new_area}' with key '{new_key}' and value '{new_value}' added to JSON file.")

def read_file_names(file_path):
            with open(file_path, 'r') as file:
                big_name_list = [line.strip() for line in file if line.strip() and not line.startswith('#')]
            return big_name_list

def bloxstrap():
    base_path = f"C:/Users/{getpass.getuser()}/AppData/Local/Bloxstrap/Modifications"
    nested_folders = ["PlatformContent", "pc", "textures", "sky"]
    
    if not os.path.exists(base_path):
        print("bloxstrap not found")
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

def delete_stuff(files_to_delete):
    for file_to_delete in files_to_delete:
        delete_file_path = os.path.join(folder_path, file_to_delete)
        if os.path.exists(delete_file_path):
            os.remove(delete_file_path)
            print(f'{file_to_delete} has been deleted.')
        else:
            print(f'{file_to_delete} not found.')

folder_path = f'C:/Users/{getpass.getuser()}/AppData/Local/Temp/Roblox/http'

mod_cache = False
pf_cache = False

mod_cache_check_path = os.path.join(folder_path, '016a313606e2f99a85bb1a91083206fc')
pf_cache_check_path = os.path.join(folder_path, '8a7090ac9b2e858f4aee9e19a0bfd562')

if os.path.exists(mod_cache_check_path): mod_cache = True
if os.path.exists(pf_cache_check_path): pf_cache = True

if mod_cache == False or pf_cache == False: print("Missing cache, join prompted experience.")
if mod_cache == False: webbrowser.open_new_tab("https://www.roblox.com/games/18504289170/texture-game")
if pf_cache == False: webbrowser.open_new_tab("https://www.roblox.com/games/292439477/Phantom-Forces")

while mod_cache == False or pf_cache == False:
    if os.path.exists(mod_cache_check_path) and mod_cache == False:
        print("Modding cache detected")
        mod_cache = True

    if os.path.exists(pf_cache_check_path) and pf_cache == False:
        print("PF cache detected")
        pf_cache = True

get_version()

with open('assets.json', 'r') as file:
    data = json.load(file)

while True:
    files_to_delete = ['']
    file_to_replace = ''
    menu = input("Enter the number corresponding to what you'd like to do:\n1: Ingame asset replacements\n2: Block (experimental, dont use)\n3: Clear Cache\n4: Change config\n5: Exit\n")
    if menu == '1':
        print("options:\n0: Custom\n1: Sleeves\n2: Gun smoke\n3: Sights\n4: Hitmarker\n5: Arm model tweaks\n6: Gun Sounds\n7: Gun skins\n8: No textures\n9: Default skyboxes\n10: Hitmarker sound\n11: Kill sounds")
        options = input("Enter option: ")
        try:
            match int(options):
                case 0: files_to_delete, file_to_replace = [input("Enter asset to change: ")], input("Enter replacement: ")
                case 1: files_to_delete, file_to_replace = ['aa33dd87fc9db92e891361e069da1849'], list_and_get_input("sleeves")
                case 2: files_to_delete, file_to_replace = ['8194373fb18740071f5e885bab349252'], list_and_get_input("gun smoke")
                case 3:
                    sight_option = input("Enter sight option:\n1: Deflex\n2: Eoflex\n3: Remove reflex and coyote background\n4: Remove delta black ring\n")
                    try:
                        match int(sight_option):
                            case 1: files_to_delete, file_to_replace = [data["sights"]["delta"]], data["sights"]["coyote"]; print(files_to_delete) # deflex
                            case 2: files_to_delete, file_to_replace = [data["sights"]["eotech 552"]], data["sights"]["coyote small"] # eoflex
                            case 3: files_to_delete, file_to_replace = ['3fc9141fc7c1167c575b9361a98f04c0', '2eaae4fe3a9fce967af993d27ad68d52'], '5873cfba79134ecfec6658f559d8f320' # clear coyote and reflex blue background
                            case 4: files_to_delete, file_to_replace = ['30c4d2bb30b6b8c9ac7cfeec5db25a85', '7d5652167ec33ed349e569a55a398705'], 'd625adff6a3d75081d11b3407b0b417c' # delta black ring
                            case _: print("Invalid option")
                    except Exception as e: print(f"Error: {e}")
                case 4: files_to_delete, file_to_replace = ['097165b476243d2095ef0a256320b06a'], list_and_get_input("hitmarker") # hitmarkers
                case 5: 
                    arm_option = input("Enter arm option:\n1: No arms\n2: No sleeves\n3: Bone arms\n4: default arms\n")
                    match int(arm_option):
                        case 1: files_to_delete, file_to_replace = read_file_names(data["arm models"]), '5873cfba79134ecfec6658f559d8f320' # no arms
                        case 2: files_to_delete, file_to_replace = ['0417f106902be46503fc75266526817a', '18ff02c763205099ce8542cebc98ae71'], 'd625adff6a3d75081d11b3407b0b417c'
                        case 3: files_to_delete, file_to_replace = ['f5b0bcba5570d196909a78c7a697467c', '7f828aee555e5e1161d4b39faddda970'], 'c9672591983da8fffedb9cec7df1e521'
                        case 4: delete_stuff(data["arm models"])
                        case _: print("Enter a Valid Option!")                     
                case 6: #sounds
                    sound_option = input("Enter sound option:\n1: Ak107\n2: Aug a2\n3: M60\n4: M16s\n5: Badger\n6: Intervention\n7: m107\n")
                    match int(sound_option):
                        case 1: files_to_delete, file_to_replace = ['56f8e4698653f8f9d9468ea4ae17efba'], list_and_get_input("sounds")
                        case 2: files_to_delete, file_to_replace = ['feb10a78b7cfb4cb20562b595237de35'], list_and_get_input("sounds")
                        case 3: files_to_delete, file_to_replace = ['9877e2301abfd337e428ba10f4a04e5e'], list_and_get_input("sounds")
                        case 4: files_to_delete, file_to_replace = ['20178d7285faf3f539d59a358bcb2e66'], list_and_get_input("sounds")
                        case 5: files_to_delete, file_to_replace = ['18a6668af40b844e4433d077cbaa94ff'], list_and_get_input("sounds")
                        case 6: files_to_delete, file_to_replace = ['632f5c38bd614dae412e2d5b17714ce2'], list_and_get_input("sounds")
                        case 7: files_to_delete, file_to_replace = ['bec24846e0b8f767bad1e7bf470093ad'], list_and_get_input("sounds")
                        case _: print("Enter a Valid Option!")                
                case 7: files_to_delete, file_to_replace = [list_and_get_input("gun skins")], list_and_get_input("custom skins")
                case 8: files_to_delete, file_to_replace = data["textures"], 'd625adff6a3d75081d11b3407b0b417c' # no textures without downside
                case 9: 
                    sky_option = input("Is Bloxstrap sky folder setup?\n1: yes\n2: no\n")
                    match int(sky_option):                    
                        case 1: files_to_delete, file_to_replace = data["skyboxes"], 'd625adff6a3d75081d11b3407b0b417c' # forced default skybox
                        case 2: bloxstrap()
                        case _: print("Enter a Valid Option!")   
                case 10: files_to_delete, file_to_replace = ['a177d2c00abd3e550b873d76c97ad960'], list_and_get_input("hitsound")
                case 11: files_to_delete, file_to_replace = data["killsound"]["default"], list_and_get_input("killsound")
                case _: print("Invalid number.")
        except Exception as e: print(f"Error: {e}")

        try:
            copy_file_path = os.path.join(folder_path, file_to_replace)
            if os.path.exists(copy_file_path):
                timestamp = time.strftime("%Y%m%d-%H%M%S")
                temp_file_path = os.path.join(folder_path, f'{file_to_replace}_{timestamp}')
                shutil.copy(copy_file_path, temp_file_path)
                print(f'{file_to_replace} has been copied to {temp_file_path}.')
                for file_to_delete in files_to_delete:
                    delete_file_path = os.path.join(folder_path, file_to_delete)
                    if os.path.exists(delete_file_path):
                        os.remove(delete_file_path)
                        print(f'{file_to_delete} has been deleted.')
                    else:
                        print(f'{file_to_delete} not found.')
                    new_file_path = os.path.join(folder_path, file_to_delete)
                    shutil.copy(temp_file_path, new_file_path)
                    print(f'{temp_file_path} has been copied to {new_file_path}.')
                os.rename(temp_file_path, copy_file_path)
                print(f'{temp_file_path} has been renamed back to {copy_file_path}.')
            else:
                print(f'{file_to_replace} not found.')

        except Exception as e:
            if e.winerror == 183: pass
            else: print(f'An error occurred: {e}')

    elif menu == '2':
        blockwarn = input("Warning: This is highly experimental and volatile to causing errors, only continue if you are aware of what youre doing.\nType 'done' to proceed, anything else will cancel.\n")
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

            print("Currently blocked:", " ".join(blockedlist))
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
                        modified_content = modified_content.replace(f"#127.0.0.1 {string_thing}.rbxcdn.com", f"127.0.0.1 {string_thing}.rbxcdn.com")
                        print("Blocked!")
                    elif f"127.0.0.1 {string_thing}.rbxcdn.com" in content:
                        modified_content = modified_content.replace(f"127.0.0.1 {string_thing}.rbxcdn.com", f"#127.0.0.1 {string_thing}.rbxcdn.com")
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
                print(f"An error occurred: {e}")
        else:
            pass

    elif menu == '3':
        resetkwarn = input("Warning: This will fully reset all tweaks and anything loaded from any game.\nType 'done' to proceed, anything else will cancel.\n")
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
                        print(f'The directory {directory} does not exist.')
                except Exception as e:
                    print(f'Error: {e}')

            delete_all_in_directory(folder_path)
            print("Rejoin relevant experiences")

    elif menu == "4":
        add_new_area()

    elif menu == '5':
        print("Exiting the program.")
        break

    else:
        print("Invalid, type a corresponding number!")
