v1.2.8

<h1 align=center>⚠️ This README file is for an outdated version of Fleasion. ⚠️</h1>
<br><br>
<h1 align=center>DISCLAIMER!</h1>

<p align=center>
  <b> This tool is NOT intended for gaining an unfair advantage or exploiting the game. <br> Do not modify enemy players. Map and Player tweaks will get you banned. <br> Custom tweaks are at your own risk. </b> <br> While misuse is possible, doing so is against the rules and will cause in-game consequences.  <br> All provided presets adhere to the terms of service and will not result in punishment. <br> A guide on obtaining relevant hashes will not be shared due to its potential for abuse.
</p>

<h1 align="center">Request & Support</h1>

<h3 align="center">
  To request a change/asset/etc. or to get help, join our <a href="https://discord.com/invite/pdtce585f6">Discord server!</a>
</h3>

<p align="center">
  <a href="https://discord.gg/hXyhKehEZF">
    <img src="https://invidget.switchblade.xyz/hXyhKehEZF" alt="Join our Discord server">
  </a>
</p>

<h1 align="center">How to use Fleasion?</h1>

<h3 align="center">
  Watch <a href="https://www.youtube.com/watch?v=P1Iva68epaU"> this tutorial </a> made by <a href=https://www.youtube.com/@FantixYT>Fantix</a>
</h3>

<p align="center">
  <a href="https://www.youtube.com/watch?v=P1Iva68epaU" target="_blank">
    <img src="https://i.ytimg.com/vi/P1Iva68epaU/maxresdefault.jpg" alt="Watch on YouTube" width="500" height="281">
  </a>
</p>



<h1 align=center>Getting ready</h1>

<li>Download the <b>Source code (zip)</b> from <a href="https://github.com/CroppingFlea479/Fleasion/releases/latest">here</a> and extract it. (you will want something like <a href=https://www.7-zip.org/download.html><b>7zip</b></a> to extract it)
<li>Run <code>run.bat</code>. It will set everything up and/or make sure you have the dependencies.
<li>The Fleasion script will then run, follow the prompts given.

<h3 align=center>If run.bat fails</h3>

  <li>1. Download the <a href=https://www.python.org/ftp/python/3.12.6/python-3.12.6-amd64.exe> latest release of Python right here </a> and install it with the <b>Add python.exe to PATH</b> checkbox checked on.
  <p align=center>
    <a>
      <img src=https://github.com/user-attachments/assets/3f3833e4-280e-44c9-9b04-21d0e9cf471f>
    </a>
  </p>
  <li>2. Launch Command Prompt by pressing a keyboard combination "<code>Win+R</code>" (Win = Windows key), typing "<code>cmd</code>" and pressing Enter.
  <li>2.1. Type <code>pip install requests</code> and press Enter.</li>
  ^ If you see this (see image below) <a href=https://github.com/CroppingFlea479/Fleasion/#if-runbat-fails>redo step 1.</a>
  <p align=center>
    <a>
      <img src=https://github.com/user-attachments/assets/52f9e445-f963-4271-aa6e-f6595413531d width=800 height=100>
    </a>
  </p>
  <li> After it finishes, download the <b>Source code (zip)</b> from <a href="https://github.com/CroppingFlea479/Fleasion/releases/latest">here</a> and extract it. (you will want something like <a href=https://www.7-zip.org/download.html><b>7zip</b></a> to extract it)
  <li> Open the extracted folder and run <code>fleasion.py</code> directly.

<p></p>

<h1 align="center">Loading Textures</h1>

  <p align=center>
    <a>
      <img src=https://github.com/user-attachments/assets/777b7b73-5328-4514-83f6-eb7276ef919b width=629 height=94>
    </a>
  </p>

<b>Fleasion requires you to load resources before you can use it.</b> <br>You will be redirected to <a href=https://www.roblox.com/games/18504289170/Texture-Game><b>Texture Game</b></a>
and <a href=https://www.roblox.com/games/292439477/Phantom-Forces><b>Phantom Forces</b></a> upon launching Fleasion to load said resources. <b>Some</b> actions will also require you to do something to load the default:
<li> <b>Sights</b> (Equip the sight on a gun)
<li> <b>In-game sounds</b> (Trigger the sound before replacing it)
<li> <b>Skins</b> (Equip the skin on a gun / Load the skin's preview in the case shop)
<li> <b>Default skyboxes</b> (Load the skyboxes by playing on maps with the corresponding skyboxes)
<li> <b>Removing textures</b> (Load the textures by playing on maps with the textures you want to remove)
<p></p>
If you are unsure of why a texture isn't loading, <a href=https://github.com/CroppingFlea479/Fleasion/#help--support>ask us!</a>

<p></p>

<h1 align=center>Features</h1>
  <p align=center>
    <a>
      <img src=https://github.com/user-attachments/assets/e4218dcb-07ff-41e4-a829-bbdb5166e9f7 alt=features width=582 height=200>
    </a>
  </p>
<li> <b>Replacement of textures/audio</b>
<li> <b>Presets:</b> Allows you to save multiple replacements to be used in bulk.
<li> <b>Blocking:</b> Highly experimental. Requires running Fleasion as admin to access. It is volatile and can break your game. Use only if you know what you're doing.
<li> <b>Cache settings:</b> Used to clear the replacements you've done. You can either clear a specific change or all changes.
<li> <b>Settings:</b> FFlags settings for Roblox. The main setting is disabling cache clearing automatically.
If you messed up, type "skip" to go back.

<h3 align="center">Bloxstrap Compatibility</h3>
For the <code>Force default skyboxes</code> feature, it's recommended to be used alongside <a href=https://bloxstrap.pizzaboxer.xyz>Bloxstrap</a>. <br>
Custom Bloxstrap skyboxes only work on maps with default skyboxes, <br> so using Fleasion to force default skyboxes allows custom Bloxstrap 
skyboxes to be used on any map.


<h3 align=center>Hashes</h3>
<p>
All of the hashes used to replace textures/audio are stored internally in <a href=https://github.com/CroppingFlea479/Fleasion/blob/main/assets.json>assets.json</a>, and are updated regularly with new additions. <br>
For a more human readable dump of some of the PF hashes, see this <a href=https://docs.google.com/spreadsheets/d/1S7GexRGkgiDXit8qabV7rYFOctO6FraZrrTm1-Rru_4/edit?usp=sharing>spreadsheet</a> compiled by <a href=https://discord.com/users/749886948579213352>Yolo</a> and <a href=https://discord.com/users/391844483970498562>Commit</a>.
</p>
<h1 align=center>Fleasion Team</h1>

<h2 align=center>
  <a>
    <a href=https://discord.com/users/776150381280886815>Crop</a> (founder)<br>
    <a href=https://discord.com/users/333184650606411776>Tyler</a>,
    <a href=https://discord.com/users/749886948579213352>Yolo</a>,
    <a href=https://discord.com/users/391844483970498562>Commit</a>,
    <a href=https://discord.com/users/1198598120775364659>3tcy</a>,
    <a href=https://discord.com/users/629024378402766900>Deco</a>,
    <a href=https://discord.com/users/898381322278551572>Fizzy</a>
  </a>
</h>
<p></p>
Distributed in the <a href=https://discord.gg/hXyhKehEZF>Fleasion Discord Server</a>
