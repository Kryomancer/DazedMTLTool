from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import os
from pathlib import Path
import re
import sys
import textwrap
import threading
import time
import traceback
import tiktoken
from ruamel.yaml import YAML

from colorama import Fore
from dotenv import load_dotenv
import openai
from retry import retry
from tqdm import tqdm

#Globals
load_dotenv()
openai.organization = os.getenv('org')
openai.api_key = os.getenv('key')

APICOST = .002 # Depends on the model https://openai.com/pricing
PROMPT = Path('prompt.txt').read_text(encoding='utf-8')
THREADS = 10 # For GPT4 rate limit will be hit if you have more than 1 thread.
LOCK = threading.Lock()
WIDTH = 50
LISTWIDTH = 60
MAXHISTORY = 10
ESTIMATE = ''
TOTALCOST = 0
TOTALTOKENS = 0
NAMESLIST = []

#tqdm Globals
BAR_FORMAT='{l_bar}{bar:10}{r_bar}{bar:-10b}'
POSITION=0
LEAVE=False

# Flags
CODE401 = True
CODE405 = False
CODE102 = True
CODE122 = False
CODE101 = False
CODE355655 = False
CODE357 = False
CODE657 = False
CODE356 = False
CODE320 = False
CODE324 = False
CODE111 = False
CODE408 = False
CODE108 = False
NAMES = True   # Output a list of all the character names found
BRFLAG = False   # If the game uses <br> instead
FIXTEXTWRAP = False  # Adjust wordwrap of text (IGNORETLTEXT must be False)
IGNORETLTEXT = True     # Leave this False if you need to adjust the wordwrap

def handleACE(filename, estimate):
    global ESTIMATE, TOTALTOKENS, TOTALCOST
    ESTIMATE = estimate

    if estimate:
        start = time.time()
        translatedData = openFiles(filename)

        # Print Result
        end = time.time()
        tqdm.write(getResultString(translatedData, end - start, filename))
        if NAMES == True:
            tqdm.write(str(NAMESLIST))
        with LOCK:
            TOTALCOST += translatedData[1] * .001 * APICOST
            TOTALTOKENS += translatedData[1]

        return getResultString(['', TOTALTOKENS, None], end - start, 'TOTAL')
    
    else:
        try:
            with open('translated/' + filename, 'w', encoding='UTF-8') as outFile:
                start = time.time()
                translatedData = openFiles(filename)

                # Print Result
                end = time.time()
                yaml=YAML(pure=True)
                yaml.width = 4096
                yaml.default_style = "'"
                yaml.dump(translatedData[0], outFile)
                tqdm.write(getResultString(translatedData, end - start, filename))
                with LOCK:
                    TOTALCOST += translatedData[1] * .001 * APICOST
                    TOTALTOKENS += translatedData[1]
        except Exception as e:
            traceback.print_exc()
            return 'Fail'

    return getResultString(['', TOTALTOKENS, None], end - start, 'TOTAL')

def openFiles(filename):
    yaml=YAML(pure=True)   # Need a yaml instance per thread.
    yaml.width = 4096
    yaml.default_style = "'"

    with open('files/' + filename, 'r', encoding='UTF-8') as f:
        data = yaml.load(f)

        # Map Files
        if 'Map' in filename and filename != 'MapInfos.json':
            translatedData = parseMap(data, filename)

        # CommonEvents Files
        elif 'CommonEvents' in filename:
            translatedData = parseCommonEvents(data, filename)

        # Actor File
        elif 'Actors' in filename:
            translatedData = parseNames(data, filename, 'Actors')

        # Armor File
        elif 'Armors' in filename:
            translatedData = parseNames(data, filename, 'Armors')

        # Weapons File
        elif 'Weapons' in filename:
            translatedData = parseNames(data, filename, 'Weapons')
        
        # Classes File
        elif 'Classes' in filename:
            translatedData = parseNames(data, filename, 'Classes')

        # Enemies File
        elif 'Enemies' in filename:
            translatedData = parseNames(data, filename, 'Enemies')

        # Items File
        elif 'Items' in filename:
            translatedData = parseThings(data, filename)

        # MapInfo File
        elif 'MapInfos' in filename:
            translatedData = parseNames(data, filename, 'MapInfos')

        # Skills File
        elif 'Skills' in filename:
            translatedData = parseSS(data, filename)

        # Troops File
        elif 'Troops' in filename:
            translatedData = parseTroops(data, filename)

        # States File
        elif 'States' in filename:
            translatedData = parseSS(data, filename)

        # System File
        elif 'System' in filename:
            translatedData = parseSystem(data, filename)

        # Scenario File
        elif 'Scenario' in filename:
            translatedData = parseScenario(data, filename)

        else:
            raise NameError(filename + ' Not Supported')
    
    return translatedData

def getResultString(translatedData, translationTime, filename):
    # File Print String
    tokenString = Fore.YELLOW + '[' + str(translatedData[1]) + \
        ' Tokens/${:,.4f}'.format(translatedData[1] * .001 * APICOST) + ']'
    timeString = Fore.BLUE + '[' + str(round(translationTime, 1)) + 's]'

    if translatedData[2] == None:
        # Success
        return filename + ': ' + tokenString + timeString + Fore.GREEN + u' \u2713 ' + Fore.RESET

    else:
        # Fail
        try:
            raise translatedData[2]
        except Exception as e:
            traceback.print_exc()
            errorString = str(e) + Fore.RED
            return filename + ': ' + tokenString + timeString + Fore.RED + u' \u2717 ' +\
                errorString + Fore.RESET

def parseMap(data, filename):
    totalTokens = 0
    totalLines = 0
    events = data['events']
    global LOCK

    # Get total for progress bar
    for key in events:
        if key is not None:
            for page in events[key]['pages']:
                totalLines += len(page['list'])
    
    with tqdm(bar_format=BAR_FORMAT, position=POSITION, total=totalLines, leave=LEAVE) as pbar:
        pbar.desc=filename
        pbar.total=totalLines
        with ThreadPoolExecutor(max_workers=THREADS) as executor:
            for key in events:
                if key is not None:
                    # This translates text above items on the map.
                    # if 'LB:' in event['note']:
                        # totalTokens += translateNote(event, r'(?<=LB:)[^u0000-u0080]+')

                    futures = [executor.submit(searchCodes, page, pbar) for page in events[key]['pages'] if page is not None]
                    for future in as_completed(futures):
                        try:
                            totalTokens += future.result()
                        except Exception as e:
                            traceback.print_exc()
                            return [data, totalTokens, e]
    return [data, totalTokens, None]

def translateNote(event, regex):
    # Regex that only matches text inside LB.
    jaString = event['note']

    match = re.findall(regex, jaString, re.DOTALL)
    if match:
        oldJAString = match[0]
        # Remove any textwrap
        jaString = re.sub(r'\n', ' ', oldJAString)

        # Translate
        response = translateGPT(jaString, 'Reply with the English translation of the hint.', True)
        translatedText = response[0]

        # Textwrap
        translatedText = textwrap.fill(translatedText, width=LISTWIDTH)

        translatedText = translatedText.replace('\"', '')
        event['note'] = event['note'].replace(oldJAString, translatedText)
        return response[1]
    return 0

def parseCommonEvents(data, filename):
    totalTokens = 0
    totalLines = 0
    global LOCK

    # Get total for progress bar
    for page in data:
        if page is not None:
            totalLines += len(page['list'])

    with tqdm(bar_format=BAR_FORMAT, position=POSITION, total=totalLines, leave=LEAVE) as pbar:
        pbar.desc=filename
        pbar.total=totalLines
        with ThreadPoolExecutor(max_workers=THREADS) as executor:
            futures = [executor.submit(searchCodes, page, pbar) for page in data if page is not None]
            for future in as_completed(futures):
                try:
                    totalTokens += future.result()
                except Exception as e:
                    traceback.print_exc()
                    return [data, totalTokens, e]
    return [data, totalTokens, None]

def parseTroops(data, filename):
    totalTokens = 0
    totalLines = 0
    global LOCK

    # Get total for progress bar
    for troop in data:
        if troop is not None:
            for page in troop['pages']:
                totalLines += len(page['list']) + 1 # The +1 is because each page has a name.

    with tqdm(bar_format=BAR_FORMAT, position=POSITION, total=totalLines, leave=LEAVE) as pbar:
        pbar.desc=filename
        pbar.total=totalLines
        for troop in data:
            if troop is not None:
                with ThreadPoolExecutor(max_workers=THREADS) as executor:
                    futures = [executor.submit(searchCodes, page, pbar) for page in troop['pages'] if page is not None]
                    for future in as_completed(futures):
                        try:
                            totalTokens += future.result()
                        except Exception as e:
                            traceback.print_exc()
                            return [data, totalTokens, e]
    return [data, totalTokens, None]
    
def parseNames(data, filename, context):
    totalTokens = 0
    totalLines = 0
    totalLines += len(data)
                
    with tqdm(bar_format=BAR_FORMAT, position=POSITION, total=totalLines, leave=LEAVE) as pbar:
            pbar.desc=filename
            pbar.total=totalLines
            for name in data:
                if name is not None:
                    try:
                        result = searchNames(name, pbar, context)       
                        totalTokens += result
                    except Exception as e:
                        traceback.print_exc()
                        return [data, totalTokens, e]
    return [data, totalTokens, None]

def parseThings(data, filename):
    totalTokens = 0
    totalLines = 0
    totalLines += len(data)
                
    with tqdm(bar_format=BAR_FORMAT, position=POSITION, total=totalLines, leave=LEAVE) as pbar:
            pbar.desc=filename
            pbar.total=totalLines
            for name in data:
                if name is not None:
                    try:
                        result = searchThings(name, pbar)       
                        totalTokens += result
                    except Exception as e:
                        traceback.print_exc()
                        return [data, totalTokens, e]
    return [data, totalTokens, None]

def parseSS(data, filename):
    totalTokens = 0
    totalLines = 0
    totalLines += len(data)
                
    with tqdm(bar_format=BAR_FORMAT, position=POSITION, total=totalLines, leave=LEAVE) as pbar:
            pbar.desc=filename
            pbar.total=totalLines
            for ss in data:
                if ss is not None:
                    try:
                        result = searchSS(ss, pbar)       
                        totalTokens += result
                    except Exception as e:
                        traceback.print_exc()
                        return [data, totalTokens, e]
    return [data, totalTokens, None]

def parseSystem(data, filename):
    totalTokens = 0
    totalLines = 0

    # Calculate Total Lines
    for term in data['terms']:
        termList = data['terms'][term]
        totalLines += len(termList)
    totalLines += len(data['game_title'])
    totalLines += len(data['weapon_types'])
    totalLines += len(data['armor_types'])
    totalLines += len(data['skill_types'])
                
    with tqdm(bar_format=BAR_FORMAT, position=POSITION, total=totalLines, leave=LEAVE) as pbar:
        pbar.desc=filename
        pbar.total=totalLines
        try:
            result = searchSystem(data, pbar)       
            totalTokens += result
        except Exception as e:
            traceback.print_exc()
            return [data, totalTokens, e]
    return [data, totalTokens, None]

def parseScenario(data, filename):
    totalTokens = 0
    totalLines = 0
    global LOCK

    # Get total for progress bar
    for page in data.items():
        totalLines += len(page[1])

    with tqdm(bar_format=BAR_FORMAT, position=POSITION, total=totalLines, leave=LEAVE) as pbar:
        pbar.desc=filename
        pbar.total=totalLines
        with ThreadPoolExecutor(max_workers=THREADS) as executor:
            futures = [executor.submit(searchCodes, page[1], pbar) for page in data.items() if page[1] is not None]
            for future in as_completed(futures):
                try:
                    totalTokens += future.result()
                except Exception as e:
                    traceback.print_exc()
                    return [data, totalTokens, e]
    return [data, totalTokens, None]

def searchThings(name, pbar):
    tokens = 0

    # Name
    nameResponse = translateGPT(name['name'], 'Reply with only the english translation of the RPG item name.', False) if 'name' in name else ''

    # Description
    descriptionResponse = translateGPT(name['description'], 'Reply with only the english translation of the description.', False) if 'description' in name else ''

    # Note
    if '<SG説明:' in name['note']:
        tokens += translateNote(name, r'<SG説明:(.*?)>')

    # Count Tokens
    tokens += nameResponse[1] if nameResponse != '' else 0
    tokens += descriptionResponse[1] if descriptionResponse != '' else 0

    # Set Data
    if 'name' in name:
        name['name'] = nameResponse[0].replace('\"', '')
    if 'description' in name:
        description = descriptionResponse[0]

        # Remove Textwrap
        description = description.replace('\n', ' ')
        description = textwrap.fill(descriptionResponse[0], LISTWIDTH)
        name['description'] = description.replace('\"', '')

    pbar.update(1)
    return tokens

def searchNames(name, pbar, context):
    tokens = 0

    # Set the context of what we are translating
    if 'Actors' in context:
        newContext = 'Reply with only the english translation of the NPC name'
    if 'Armors' in context:
        newContext = 'Reply with only the english translation of the RPG equipment name'
    if 'Classes' in context:
        newContext = 'Reply with only the english translation of the RPG class name'
    if 'MapInfos' in context:
        newContext = 'Reply with only the english translation of the location name'
    if 'Enemies' in context:
        newContext = 'Reply with only the english translation of the enemy NPC name'
    if 'Weapons' in context:
        newContext = 'Reply with only the english translation of the RPG weapon name'

    # Extract Data
    responseList = []
    responseList.append(translateGPT(name['name'], newContext, True))
    if 'Actors' in context:
        responseList.append(translateGPT(name['description'], '', True))
        responseList.append(translateGPT(name['nickname'], 'Reply with ONLY the english translation of the NPC nickname', True))

    if 'Armors' in context or 'Weapons' in context:
        if 'description' in name:
            responseList.append(translateGPT(name['description'], '', True))
        else:
            responseList.append(['', 0])
        if 'hint' in name['note']:
            tokens += translateNote(name, r'<Info Text Bottom>\n([\s\S]*?)\n</Info Text Bottom>')

    if 'Enemies' in context:
        if 'variable_update_skill' in name['note']:
            tokens += translateNote(name, r'111:(.+?)\n')

        if 'desc2' in name['note']:
            tokens += translateNote(name, r'<desc2:([^>]*)>')

        if 'desc3' in name['note']:
            tokens += translateNote(name, r'<desc3:([^>]*)>')

    # Extract all our translations in a list from response
    for i in range(len(responseList)):
        tokens += responseList[i][1]
        responseList[i] = responseList[i][0]

    # Set Data
    name['name'] = responseList[0].replace('\"', '')
    if 'Actors' in context:
        translatedText = textwrap.fill(responseList[1], LISTWIDTH)
        name['profile'] = translatedText.replace('\"', '')
        translatedText = textwrap.fill(responseList[2], LISTWIDTH)
        name['nickname'] = translatedText.replace('\"', '')
        if '<特徴1:' in name['note']:
            tokens += translateNote(name, r'<特徴1:([^>]*)>')

    if 'Armors' in context or 'Weapons' in context:
        translatedText = textwrap.fill(responseList[1], LISTWIDTH)
        if 'description' in name:
            name['description'] = translatedText.replace('\"', '')
            if '<SG説明:' in name['note']:
                tokens += translateNote(name, r'<Info Text Bottom>\n([\s\S]*?)\n</Info Text Bottom>')
    pbar.update(1)

    return tokens

def searchCodes(page, pbar):
    translatedText = ''
    currentGroup = []
    textHistory = []
    maxHistory = MAXHISTORY
    tokens = 0
    speaker = ''
    speakerVar = ''
    nametag = ''
    match = []
    syncIndex = 0
    global LOCK
    global NAMESLIST

    try:
        if 'list' in page:
            codeList = page['list']
        else:
            codeList = page
        for i in range(len(codeList)):
            with LOCK:  
                if syncIndex > i:
                    i = syncIndex
                pbar.update(1)
                if len(codeList) <= i:
                    break

            ### All the codes are here which translate specific functions in the MAP files.
            ### IF these crash or fail your game will do the same. Use the flags to skip codes.

            ## Event Code: 401 Show Text
            if codeList[i]['c'] == 401 and CODE401 == True or codeList[i]['c'] == 405 and CODE405:  
                # Use this to place text later
                code = codeList[i]['c']
                j = i

                # Grab String  
                jaString = codeList[i]['p'][0]
                firstJAString = jaString

                # If there isn't any Japanese in the text just skip
                if IGNORETLTEXT == True:
                    if not re.search(r'[一-龠]+|[ぁ-ゔ]+|[ァ-ヴー]+', jaString):
                        # Keep textHistory list at length maxHistory
                        textHistory.append('\"' + jaString + '\"')
                        if len(textHistory) > maxHistory:
                            textHistory.pop(0)
                        currentGroup = []  
                        continue

                # Using this to keep track of 401's in a row. Throws IndexError at EndOfList (Expected Behavior)
                currentGroup.append(jaString)

                if len(codeList) > i+1:
                    while (codeList[i+1]['c'] == 401 or codeList[i+1]['c'] == 405):
                        codeList[i]['p'][0] = ''
                        codeList[i]['c'] = 0
                        i += 1

                        jaString = codeList[i]['p'][0]
                        currentGroup.append(jaString)

                        # Make sure not the end of the list.
                        if len(codeList) <= i+1:
                            break

                # Join up 401 groups for better translation.
                if len(currentGroup) > 0:
                    finalJAString = ''.join(currentGroup)
                    oldjaString = finalJAString

                    # Color Regex: ^([\\]+[cC]\[[0-9]\]+(.+?)[\\]+[cC]\[[0]\])
                    matchList = re.findall(r'(.*?([\\]+[nN]<(.+?)>).*)', finalJAString)
                    if len(matchList) > 0:  
                        response = translateGPT(matchList[0][2], 'Reply with only the english translation of the NPC name', True)
                        tokens += response[1]
                        speaker = response[0].strip('.')
                        nametag = matchList[0][1].replace(matchList[0][2], speaker)
                        finalJAString = finalJAString.replace(matchList[0][1], '')

                        # Set next item as dialogue
                        if (codeList[j + 1]['c'] == -1 and len(codeList[j + 1]['p']) > 0) or codeList[j + 1]['c'] == -1:
                            # Set name var to top of list
                            codeList[j]['p'][0] = nametag
                            codeList[j]['c'] = code

                            j += 1
                            codeList[j]['p'][0] = finalJAString
                            codeList[j]['c'] = code
                            nametag = ''
                        else:
                            # Set nametag in string
                            codeList[j]['p'][0] = nametag + finalJAString
                            codeList[j]['c'] = code
                        
                        # Put names in list
                        if speaker not in NAMESLIST:
                            with LOCK:
                                NAMESLIST.append(speaker)
                    elif '\\kw' in finalJAString:
                        match = re.findall(r'\\+kw\[[0-9]+\]', finalJAString)
                        if len(match) != 0:
                            if '1' in match[0]:
                                speaker = 'Ayako Nagatsuki'
                            if '2' in match[0]:
                                speaker = 'Rei'
                            
                            # Set name var to top of list
                            codeList[j]['p'][0] = match[0]
                            codeList[j]['c'] = code

                            # Set next item as dialogue
                            j += 1
                            codeList[j]['p'][0] = match[0]
                            codeList[j]['c'] = code

                            # Remove nametag from final string
                            finalJAString = finalJAString.replace(match[0], '')  
                    elif '\\nc' in finalJAString:     
                        matchList = re.findall(r'(\\+nc<(.*?)>)(.+)?', finalJAString)    
                        if len(matchList) != 0:    
                            # Translate Speaker  
                            response = translateGPT(matchList[0][1], 'Reply with only the english translation of the NPC name', True)
                            tokens += response[1]
                            speaker = response[0].strip('.')
                            nametag = matchList[0][0].replace(matchList[0][1], speaker)
                            finalJAString = finalJAString.replace(matchList[0][0], '')

                            # Set dialogue
                            codeList[j]['p'][0] = matchList[0][2]
                            codeList[j]['c'] = 401

                            # Remove nametag from final string
                            finalJAString = finalJAString.replace(nametag, '')
                    elif '\\nw' in finalJAString or '\\NW' in finalJAString:
                        matchList = re.findall(r'([\\]+[nN][wW]\[(.+?)\])(.+)', finalJAString)    
                        if len(matchList) != 0:    
                            response = translateGPT(matchList[0][1], 'Reply with only the english translation of the NPC name', True)
                        else:
                            print('Regex Failed')
                        tokens += response[1]
                        speaker = response[0].strip('.')

                        # Set Nametag and Remove from Final String
                        nametag = matchList[0][0].replace(matchList[0][1], speaker)
                        finalJAString = finalJAString.replace(matchList[0][0], '')

                        # Set next item as dialogue
                        if (codeList[j + 1]['c'] == 401 and len(codeList[j + 1]['p']) > 0) or (codeList[j + 1]['c'] == 0 and len(codeList[j + 1]['p']) > 0):
                            # Set name var to top of list
                            codeList[j]['p'][0] = nametag
                            codeList[j]['c'] = code

                            j += 1
                            codeList[j]['p'][0] = finalJAString
                            codeList[j]['c'] = code
                            nametag = ''
                        else:
                            # Set nametag in string
                            codeList[j]['p'][0] = nametag + finalJAString
                            codeList[j]['c'] = code
                    ### Only for Specific games where name is surrounded by brackets.
                    # elif '【' in finalJAString:
                    #     matchList = re.findall(r'(.+?【(.+?)】.+?)(「.+)', finalJAString)    
                    #     if len(matchList) != 0:    
                    #         response = translateGPT(matchList[0][1], 'Reply with only the english translation of the NPC name', True)
                    #     else:
                    #         print('wtf')
                    #     tokens += response[1]
                    #     speaker = response[0].strip('.')

                    #     # Set Nametag and Remove from Final String
                    #     nametag = matchList[0][0].replace(matchList[0][1], speaker)
                    #     finalJAString = finalJAString.replace(matchList[0][0], '')

                    #     # Set next item as dialogue
                    #     if (codeList[j + 1]['c'] == 401 and len(codeList[j + 1]['p']) > 0) or codeList[j + 1]['c'] == 0:
                    #         # Set name var to top of list
                    #         codeList[j]['p'][0] = nametag
                    #         codeList[j]['c'] = code

                    #         j += 1
                    #         codeList[j]['p'][0] = finalJAString
                    #         codeList[j]['c'] = code
                    #         nametag = ''
                    #     else:
                    #         # Set nametag in string
                    #         codeList[j]['p'][0] = nametag + finalJAString
                    #         codeList[j]['c'] = code

                    # Remove any textwrap
                    if FIXTEXTWRAP == True:
                        finalJAString = re.sub(r'\n', ' ', finalJAString)
                        finalJAString = finalJAString.replace('<br>', ' ')

                    # Remove Extra Stuff
                    finalJAString = finalJAString.replace('ﾞ', '')
                    finalJAString = finalJAString.replace('。', '.')
                    finalJAString = finalJAString.replace('・', '.')
                    finalJAString = finalJAString.replace('‶', '')
                    finalJAString = finalJAString.replace('”', '')
                    finalJAString = finalJAString.replace('―', '-')
                    finalJAString = finalJAString.replace('…', '...')
                    finalJAString = finalJAString.replace('　', '')
                    finalJAString = finalJAString.replace('\\#', '')

                    # Remove any RPGMaker Code at start
                    ffMatchList = re.findall(r'[\\]+[fF]+\[.+?\]', finalJAString)
                    if len(ffMatchList) > 0:
                        finalJAString = finalJAString.replace(ffMatchList[0], '')
                        nametag += ffMatchList[0]

                    ### Remove format codes
                    # Furigana
                    rcodeMatch = re.findall(r'([\\]+r\[(.+?),.+?\])', finalJAString)
                    if len(rcodeMatch) > 0:
                        for match in rcodeMatch:
                            finalJAString = finalJAString.replace(match[0],match[1])
                    # # Formatting Codes
                    if finalJAString == 'あ、\\!あんな風に、おちんちん入ってるんだ...':
                        print('t')
                    formatMatch = re.findall(r'[\\]+[!><.]', finalJAString)
                    if len(formatMatch) > 0:
                        for match in formatMatch:
                            finalJAString = finalJAString.replace(match, '')


                    # Translate
                    if speaker == '' and finalJAString != '':
                        response = translateGPT(finalJAString, 'Past Translated Text: ' + '|\n\n'.join(textHistory), True)
                        tokens += response[1]
                        translatedText = response[0]
                        textHistory.append('\"' + translatedText + '\"')
                    elif finalJAString != '':
                        response = translateGPT(speaker + ': ' + finalJAString, 'Past Translated Text: ' + '|\n\n'.join(textHistory), True)
                        tokens += response[1]
                        translatedText = response[0]
                        textHistory.append('\"' + translatedText + '\"')

                        # Remove added speaker
                        translatedText = re.sub(r'^.+?:\s?', '', translatedText)
                        speaker = ''                
                    else:
                        translatedText = finalJAString    

                    # Textwrap
                    if '\n' not in translatedText and '<br>' not in translatedText:
                        translatedText = textwrap.fill(translatedText, width=WIDTH)
                        if BRFLAG == True:
                            translatedText = translatedText.replace('\n', '<br>')   

                    # Add Beginning Text
                    translatedText = nametag + translatedText
                    nametag = ''

                    # Set Data
                    translatedText = translatedText.replace('\"', '')
                    translatedText = translatedText.replace('\\CL ', '\\CL')
                    translatedText = translatedText.replace('\\CL', '\\CL ')
                    codeList[i]['p'][0] = ''
                    codeList[i]['c'] = 0
                    codeList[j]['p'][0] = translatedText
                    codeList[j]['c'] = code
                    speaker = ''
                    match = []
                    syncIndex = i + 1

                    # Keep textHistory list at length maxHistory
                    if len(textHistory) > maxHistory:
                        textHistory.pop(0)
                    currentGroup = []              

            ## Event Code: 122 [Set Variables]
            if codeList[i]['c'] == 122 and CODE122 == True:  
                # This is going to be the var being set. (IMPORTANT)
                varNum = codeList[i]['p'][0]
                if varNum != 319:
                    continue
                  
                jaString = codeList[i]['p'][4]
                if type(jaString) != str:
                    continue
                
                # Definitely don't want to mess with files
                if '■' in jaString or '_' in jaString:
                    continue

                # Definitely don't want to mess with files
                # if '\"' not in jaString:
                #     continue

                # Need to remove outside code and put it back later
                matchList = re.findall(r"[\'\"\`](.*)[\'\"\`]", jaString)
                
                for match in matchList:
                    # Remove Textwrap
                    match = match.replace('\\n', ' ')
                    response = translateGPT(match, 'Reply with the English translation.', True)
                    translatedText = response[0]
                    tokens += response[1]

                    # Replace
                    translatedText = jaString.replace(jaString, translatedText)

                    # Remove characters that may break scripts
                    charList = ['.', '\"', '\\n']
                    for char in charList:
                        translatedText = translatedText.replace(char, '')
                
                # Textwrap
                translatedText = textwrap.fill(translatedText, width=70)
                translatedText = translatedText.replace('\n', '\\n')
                # translatedText = translatedText.replace('\'', '\\\'')
                translatedText = '\"' + translatedText + '\"'

                # Set Data
                codeList[i]['p'][4] = translatedText

        ## Event Code: 357 [Picture Text] [Optional]
            if codeList[i]['c'] == 357 and CODE357 == True:    
                if 'message' in codeList[i]['p'][3]:
                    jaString = codeList[i]['p'][3]['message']
                    if type(jaString) != str:
                        continue
                    
                    # Definitely don't want to mess with files
                    if '_' in jaString:
                        continue

                    # If there isn't any Japanese in the text just skip
                    if not re.search(r'[一-龠]+|[ぁ-ゔ]+|[ァ-ヴー]+', jaString):
                        continue

                    # Need to remove outside code and put it back later
                    oldjaString = jaString
                    startString = re.search(r'^[^一-龠ぁ-ゔァ-ヴー【】（）「」a-zA-ZＡ-Ｚ０-９\\]+', jaString)
                    finalJAString = re.sub(r'^[^一-龠ぁ-ゔァ-ヴー【】（）「」a-zA-ZＡ-Ｚ０-９\\]+', '', jaString)
                    if startString is None: startString = ''
                    else:  startString = startString.group()

                    # Remove any textwrap
                    finalJAString = re.sub(r'\n', ' ', finalJAString)

                    # Translate
                    response = translateGPT(finalJAString, '', True)
                    tokens += response[1]
                    translatedText = response[0]

                    # Textwrap
                    translatedText = textwrap.fill(translatedText, width=WIDTH)

                    # Set Data
                    codeList[i]['p'][3]['message'] = startString + translatedText
            
        ## Event Code: 657 [Picture Text] [Optional]
            if codeList[i]['c'] == 657 and CODE657 == True:    
                if 'text' in codeList[i]['p'][0]:
                    jaString = codeList[i]['p'][0]
                    if type(jaString) != str:
                        continue
                    
                    # Definitely don't want to mess with files
                    if '_' in jaString:
                        continue

                    # If there isn't any Japanese in the text just skip
                    if not re.search(r'[一-龠]+|[ぁ-ゔ]+|[ァ-ヴー]+', jaString):
                        continue

                    # Remove outside text
                    startString = re.search(r'^[^一-龠ぁ-ゔァ-ヴー\<\>【】\\]+', jaString)
                    jaString = re.sub(r'^[^一-龠ぁ-ゔァ-ヴー\<\>【】\\]+', '', jaString)
                    endString = re.search(r'[^一-龠ぁ-ゔァ-ヴー\<\>【】。！？\\]+$', jaString)
                    jaString = re.sub(r'[^一-龠ぁ-ゔァ-ヴー\<\>【】。！？\\]+$', '', jaString)
                    if startString is None: startString = ''
                    else:  startString = startString.group()
                    if endString is None: endString = ''
                    else: endString = endString.group()

                    # Remove any textwrap
                    jaString = re.sub(r'\n', ' ', jaString)

                    # Translate
                    response = translateGPT(jaString, '', True)
                    tokens += response[1]
                    translatedText = response[0]

                    # Remove characters that may break scripts
                    charList = ['.', '\"', "'"]
                    for char in charList:
                        translatedText = translatedText.replace(char, '')

                    # Textwrap
                    translatedText = textwrap.fill(translatedText, width=WIDTH)
                    translatedText = startString + translatedText + endString

                    # Set Data
                    if '\\' in jaString:
                        print('Hi')
                    codeList[i]['p'][0] = translatedText

        ## Event Code: 101 [Name] [Optional]
            if codeList[i]['c'] == 101 and CODE101 == True:    
                jaString = codeList[i]['p'][0]
                if type(jaString) != str:
                    continue

                # Force Speaker
                if 'natu' in jaString:
                    speaker = 'Natsu'
                    continue
                elif 'kotohana' in jaString:
                    speaker = 'Kotohana'
                    continue
                elif 'aoi' in jaString:
                    speaker = 'Aoi'
                    continue
                elif 'misaki' in jaString:
                    speaker = 'Misaki'
                    continue
                elif 'koume' in jaString:
                    speaker = 'Koume'
                    continue
                elif 'titose' in jaString:
                    speaker = 'Chitose'
                    continue
                else:
                    speaker = 'Unknown'
                
                # Definitely don't want to mess with files
                if '_' in jaString:
                    continue

                # If there isn't any Japanese in the text just skip
                if not re.search(r'[一-龠]+|[ぁ-ゔ]+|[ァ-ヴー]+', jaString):
                    speaker = jaString
                    continue

                # Need to remove outside code and put it back later
                startString = re.search(r'^[^一-龠ぁ-ゔァ-ヴー\<\>【】]+', jaString)
                jaString = re.sub(r'^[^一-龠ぁ-ゔァ-ヴー\<\>【】]+', '', jaString)
                endString = re.search(r'[^一-龠ぁ-ゔァ-ヴー\<\>【】。！？]+$', jaString)
                jaString = re.sub(r'[^一-龠ぁ-ゔァ-ヴー\<\>【】。！？]+$', '', jaString)
                if startString is None: startString = ''
                else:  startString = startString.group() + ' '
                if endString is None: endString = ''
                else: endString = endString.group()

                # Translate
                response = translateGPT(jaString, 'Reply with only the english translation of the NPC name.', False)
                tokens += response[1]
                translatedText = response[0]

                # Remove characters that may break scripts
                charList = ['.', '\"']
                for char in charList:
                    translatedText = translatedText.replace(char, '')

                translatedText = startString + translatedText + endString

                # Set Data
                speaker = translatedText
                codeList[i]['p'][4] = translatedText
                if speaker not in NAMESLIST:
                    with LOCK:
                        NAMESLIST.append(speaker)

            ## Event Code: 355 or 655 Scripts [Optional]
            if (codeList[i]['c'] == 355 or codeList[i]['c'] == 655) and CODE355655 == True:
                jaString = codeList[i]['p'][0]

                # If there isn't any Japanese in the text just skip
                if not re.search(r'[一-龠]+|[ぁ-ゔ]+|[ァ-ヴー]+', jaString):
                    continue

                # Want to translate this script
                if codeList[i]['c'] == 355 and '$gameSystem.addLog' not in jaString:
                    continue

                # Don't want to touch certain scripts
                if codeList[i]['c'] == 655 and '$gameSystem.addLog' not in jaString:
                    continue

                # Need to remove outside code and put it back later
                startString = re.search(r'^[^一-龠ぁ-ゔァ-ヴー\<\>【】（）「」『』]+', jaString)
                jaString = re.sub(r'^[^一-龠ぁ-ゔァ-ヴー\<\>【】（）「」『』]+', '', jaString)
                endString = re.search(r'[^一-龠ぁ-ゔァ-ヴー\<\>【】（）「」『』。！？]+$', jaString)
                jaString = re.sub(r'[^一-龠ぁ-ゔァ-ヴー\<\>【】（）「」『』。！？]+$', '', jaString)
                if startString is None: startString = ''
                else:  startString = startString.group()
                if endString is None: endString = ''
                else: endString = endString.group()

                # Translate
                response = translateGPT(jaString, 'Reply with the English Translation of the text.', True)
                tokens += response[1]
                translatedText = response[0]

                # Remove characters that may break scripts
                charList = ['\"', "\'"]
                for char in charList:
                    translatedText = translatedText.replace(char, '')
                
                # Set Data
                translatedText = startString + translatedText + endString
                codeList[i]['p'][0] = translatedText

        ## Event Code: 408 (Script)
            if (codeList[i]['c'] == 408) and CODE408 == True:
                jaString = codeList[i]['p'][0]

                # If there isn't any Japanese in the text just skip
                if not re.search(r'[一-龠]+|[ぁ-ゔ]+|[ァ-ヴー]+', jaString):
                    continue

                # Want to translate this script
                # if 'ans:' not in jaString:
                #     continue

                # Need to remove outside code and put it back later
                startString = re.search(r'^[^一-龠ぁ-ゔァ-ヴー【】]+', jaString)
                jaString = re.sub(r'^[^一-龠ぁ-ゔァ-ヴー【】]+', '', jaString)
                endString = re.search(r'[^一-龠ぁ-ゔァ-ヴー【】。、…！？]+$', jaString)
                jaString = re.sub(r'[^一-龠ぁ-ゔァ-ヴー【】。、…！？]+$', '', jaString)
                if startString is None: startString = ''
                else:  startString = startString.group()
                if endString is None: endString = ''
                else: endString = endString.group()

                # Translate
                response = translateGPT(jaString, '', True)
                tokens += response[1]
                translatedText = response[0]

                # Remove characters that may break scripts
                charList = ['.', '\"']
                for char in charList:
                    translatedText = translatedText.replace(char, '')

                translatedText = startString + translatedText + endString

                translatedText = translatedText.replace('"', '\"')

                # Set Data
                codeList[i]['p'][0] = translatedText

            ## Event Code: 108 (Script)
            if (codeList[i]['c'] == 108) and CODE108 == True:
                jaString = codeList[i]['p'][0]

                # If there isn't any Japanese in the text just skip
                if not re.search(r'[一-龠]+|[ぁ-ゔ]+|[ァ-ヴー]+', jaString):
                    continue

                # Want to translate this script
                if 'text_indicator : ' not in jaString:
                    continue

                # Need to remove outside code and put it back later
                matchList = re.findall(r'text_indicator : (.+)', jaString)

                # Translate
                if len(matchList) > 0:
                    response = translateGPT(matchList[0], 'Reply with the English translation of the Location Title', True)
                    tokens += response[1]
                    translatedText = response[0]

                    # Remove characters that may break scripts
                    charList = ['.', '\"']
                    for char in charList:
                        translatedText = translatedText.replace(char, '')

                    translatedText = jaString.replace(matchList[0], translatedText)
                    translatedText = translatedText.replace('"', '\"')

                    # Set Data
                    codeList[i]['p'][0] = translatedText

            ## Event Code: 356
            if codeList[i]['c'] == 356 and CODE356 == True:
                jaString = codeList[i]['p'][0]
                oldjaString = jaString

                # Grab Speaker
                if 'Tachie showName' in jaString:
                    matchList = re.findall(r'Tachie showName (.+)', jaString)
                    if len(matchList) > 0:
                        # Translate
                        response = translateGPT(matchList[0], 'Reply with the English translation of the NPC name.', True)
                        translatedText = response[0]
                        tokens += response[1]

                        # Set Text
                        speaker = translatedText
                        speaker = speaker.replace(' ', ' ')
                        codeList[i]['p'][0] = jaString.replace(matchList[0], speaker)
                    continue

                # If there isn't any Japanese in the text just skip
                if not re.search(r'[一-龠]+|[ぁ-ゔ]+|[ァ-ヴー]+', jaString):
                    continue

                # Want to translate this script
                if 'D_TEXT ' in jaString:
                    # Remove any textwrap
                    jaString = re.sub(r'\n', '_', jaString)

                    # Capture Arguments and text
                    dtextList = re.findall(r'D_TEXT\s(.+?)\s|D_TEXT\s(.+)', jaString)
                    if len(dtextList) > 0:
                        if dtextList[0][0] != '':
                            dtext = dtextList[0][0]
                        else:
                            dtext = dtextList[0][1]
                        originalDTEXT = dtext

                        # Remove underscores
                        dtext = re.sub(r'_', ' ', dtext)

                        # Using this to keep track of 401's in a row. Throws IndexError at EndOfList (Expected Behavior)
                        currentGroup.append(dtext)

                        while (codeList[i+1]['c'] == 356):
                            # Want to translate this script
                            if 'D_TEXT ' not in codeList[i+1]['p'][0]:
                                break

                            codeList[i]['p'][0] = ''
                            i += 1
                            jaString = codeList[i]['p'][0]
                            dtextList = re.findall(r'D_TEXT\s(.+?)\s|D_TEXT\s(.+)', jaString)
                            if len(dtextList) > 0:
                                if dtextList[0][0] != '':
                                    dtext = dtextList[0][0]
                                else:
                                    dtext = dtextList[0][1]
                                currentGroup.append(dtext)

                        # Join up 356 groups for better translation.
                        if len(currentGroup) > 0:
                            finalJAString = ' '.join(currentGroup)
                        else:
                            finalJAString = dtext

                        # Clear Group
                        currentGroup = [] 
                    
                        # Remove any textwrap
                        jaString = re.sub(r'\n', '_', jaString)

                        # Translate
                        response = translateGPT(finalJAString, 'Reply with the English Translation.', True)
                        translatedText = response[0]
                        tokens += response[1]

                        # Remove characters that may break scripts
                        charList = ['.', '\"']
                        for char in charList:
                            translatedText = translatedText.replace(char, '')
                        
                        # Cant have spaces?
                        translatedText = translatedText.replace(' ', '_')
                    
                        # Put Args Back
                        translatedText = jaString.replace(originalDTEXT, translatedText)

                        # Set Data
                        codeList[i]['p'][0] = translatedText
                    else:
                        continue

            ### Event Code: 102 Show Choice
            if codeList[i]['c'] == 102 and CODE102 == True:
                for choice in range(len(codeList[i]['p'][0])):
                    jaString = codeList[i]['p'][0][choice]
                    jaString = jaString.replace(' 。', '.')

                    # Need to remove outside code and put it back later
                    startString = re.search(r'^en.+\)\s|^en.+\)|^if.+\)\s|^if.+\)', jaString)
                    jaString = re.sub(r'^en.+\)\s|^en.+\)|^if.+\)\s|^if.+\)', '', jaString)
                    endString = re.search(r'\sen.+$|en.+$|\sif.+$|if.+$', jaString)
                    jaString = re.sub(r'\sen.+$|en.+$|\sif.+$|if.+$', '', jaString)
                    if startString is None: startString = ''
                    else:  startString = startString.group()
                    if endString is None: endString = ''
                    else: endString = endString.group()

                    if len(textHistory) > 0:
                        response = translateGPT(jaString, 'Keep your translation as brief as possible. Previous text for context: ' + textHistory[len(textHistory)-1] + '\n\nReply in the style of a dialogue option.', True)
                        translatedText = response[0]
                    else:
                        response = translateGPT(jaString, 'Keep your translation as brief as possible.\n\nStyle: dialogue option.', True)
                        translatedText = response[0]

                    # Remove characters that may break scripts
                    charList = ['.', '\"', '\\n']
                    for char in charList:
                        translatedText = translatedText.replace(char, '')

                    # Set Data
                    tokens += response[1]
                    codeList[i]['p'][0][choice] = startString + translatedText + endString

            ### Event Code: 111 Script
            if codeList[i]['c'] == 111 and CODE111 == True:
                for j in range(len(codeList[i]['p'])):
                    jaString = codeList[i]['p'][j]

                    # Check if String
                    if type(jaString) != str:
                        continue

                    # Only TL the Game Variable
                    if '$gameVariables' not in jaString:
                        continue

                    # This is going to be the var being set. (IMPORTANT)
                    if '1045' not in jaString:
                        continue

                    # Need to remove outside code and put it back later
                    matchList = re.findall(r"'(.*?)'", jaString)
                    
                    for match in matchList:
                        response = translateGPT(match, '', True)
                        translatedText = response[0]
                        tokens += response[1]

                        # Remove characters that may break scripts
                        charList = ['.', '\"', '\'', '\\n']
                        for char in charList:
                            translatedText = translatedText.replace(char, '')

                        jaString = jaString.replace(match, translatedText)

                    # Set Data
                    translatedText = jaString
                    codeList[i]['p'][j] = translatedText

            ### Event Code: 320 Set Variable
            if codeList[i]['c'] == 320 and CODE320 == True:                
                jaString = codeList[i]['p'][1]
                if type(jaString) != str:
                    continue
                
                # Definitely don't want to mess with files
                if '■' in jaString or '_' in jaString:
                    continue

                # If there isn't any Japanese in the text just skip
                if not re.search(r'[一-龠]+|[ぁ-ゔ]+|[ァ-ヴー]+', jaString):
                    continue
                
                response = translateGPT(jaString, 'Reply with the English translation of the NPC name.', True)
                translatedText = response[0]
                tokens += response[1]

                # Remove characters that may break scripts
                charList = ['.', '\"', '\'', '\\n']
                for char in charList:
                    translatedText = translatedText.replace(char, '')

                # Set Data
                codeList[i]['p'][1] = translatedText

    except IndexError as e:
        # This is part of the logic so we just pass it
        traceback.print_exc()
        # raise Exception(str(e) + '|Line:' + tracebackLineNo)  
    except Exception as e:
        traceback.print_exc()
        raise Exception(str(e) + 'Failed to translate: ' + oldjaString)  
                
    # Append leftover groups in 401
    if len(currentGroup) > 0:
        # Translate
        response = translateGPT(finalJAString, 'Previous Translated Text for Context: ' + '\n\n'.join(textHistory), True)
        tokens += response[1]
        translatedText = response[0]

        # TextHistory is what we use to give GPT Context, so thats appended here.
        textHistory.append('\"' + translatedText + '\"')

        # Textwrap
        translatedText = textwrap.fill(translatedText, width=WIDTH)

        # Set Data
        translatedText = translatedText.replace('ッ', '')
        translatedText = translatedText.replace('っ', '')
        translatedText = translatedText.replace('\"', '')
        codeList[i]['p'][0] = translatedText
        speaker = ''
        match = []

        # Keep textHistory list at length maxHistory
        if len(textHistory) > maxHistory:
            textHistory.pop(0)
        currentGroup = []    

    return tokens

def searchSS(state, pbar):
    '''Searches skills and states json files'''
    tokens = 0

    # Name
    nameResponse = translateGPT(state['name'], 'Reply with only the english translation of the RPG Skill name.', True) if 'name' in state else ''

    # Description
    descriptionResponse = translateGPT(state['description'], 'Reply with only the english translation of the description.', True) if 'description' in state else ''

    # Messages
    message1Response = ''
    message4Response = ''
    message2Response = ''
    message3Response = ''
    
    if 'message1' in state:
        if len(state['message1']) > 0 and state['message1'][0] in ['は', 'を', 'の']:
            message1Response = translateGPT('Taro' + state['message1'], 'reply with only the gender neutral english translation of the action. Always start the sentence with Taro.', True)
        else:
            message1Response = translateGPT(state['message1'], 'reply with only the gender neutral english translation', True)

    if 'message2' in state:
        if len(state['message2']) > 0 and state['message2'][0] in ['は', 'を', 'の']:
            message2Response = translateGPT('Taro' + state['message2'], 'reply with only the gender neutral english translation of the action. Always start the sentence with Taro.', True)
        else:
            message2Response = translateGPT(state['message2'], 'reply with only the gender neutral english translation', True)

    if 'message3' in state:
        if len(state['message3']) > 0 and state['message3'][0] in ['は', 'を', 'の']:
            message3Response = translateGPT('Taro' + state['message3'], 'reply with only the gender neutral english translation of the action. Always start the sentence with Taro.', True)
        else:
            message3Response = translateGPT(state['message3'], 'reply with only the gender neutral english translation', True)

    if 'message4' in state:
        if len(state['message4']) > 0 and state['message4'][0] in ['は', 'を', 'の']:
            message4Response = translateGPT('Taro' + state['message4'], 'reply with only the gender neutral english translation of the action. Always start the sentence with Taro.', True)
        else:
            message4Response = translateGPT(state['message4'], 'reply with only the gender neutral english translation', True)

    # if 'note' in state:
    if 'help' in state['note']:
        tokens += translateNote(state, r'<help:([^>]*)>')
    
    # Count Tokens
    tokens += nameResponse[1] if nameResponse != '' else 0
    tokens += descriptionResponse[1] if descriptionResponse != '' else 0
    tokens += message1Response[1] if message1Response != '' else 0
    tokens += message2Response[1] if message2Response != '' else 0
    tokens += message3Response[1] if message3Response != '' else 0
    tokens += message4Response[1] if message4Response != '' else 0

    # Set Data
    if 'name' in state:
        state['name'] = nameResponse[0].replace('\"', '')
    if 'description' in state:
        # Textwrap
        translatedText = descriptionResponse[0]
        translatedText = textwrap.fill(translatedText, width=LISTWIDTH)
        state['description'] = translatedText.replace('\"', '')
    if 'message1' in state:
        state['message1'] = message1Response[0].replace('\"', '').replace('Taro', '')
    if 'message2' in state:
        state['message2'] = message2Response[0].replace('\"', '').replace('Taro', '')
    if 'message3' in state:
        state['message3'] = message3Response[0].replace('\"', '').replace('Taro', '')
    if 'message4' in state:
        state['message4'] = message4Response[0].replace('\"', '').replace('Taro', '')

    pbar.update(1)
    return tokens

def searchSystem(data, pbar):
    tokens = 0
    context = 'Reply with only the english translation of the UI textbox'

    # Title
    # response = translateGPT(data['game_title'], ' Reply with the English translation of the game title name', False)
    # tokens += response[1]
    # data['game_title'] = response[0].strip('.')
    # pbar.update(1)
    
    # Terms
    for term in data['terms']:
        if term != 'messages':
            termList = data['terms'][term]
            for i in range(len(termList)):  # Last item is a messages object
                if termList[i] is not None:
                    response = translateGPT(termList[i], context, False)
                    tokens += response[1]
                    termList[i] = response[0].replace('\"', '')
                    pbar.update(1)

    # Armor Types
    for i in range(len(data['armor_types'])):
        response = translateGPT(data['armor_types'][i], 'Reply with only the english translation of the armor type', False)
        tokens += response[1]
        data['armor_types'][i] = response[0].replace('\"', '')
        pbar.update(1)

    # Skill Types
    for i in range(len(data['skill_types'])):
        response = translateGPT(data['skill_types'][i], 'Reply with only the english translation', False)
        tokens += response[1]
        data['skill_types'][i] = response[0].replace('\"', '')
        pbar.update(1)

    # Variables (Optional ususally)
    # for i in range(len(data['variables'])):
    #     response = translateGPT(data['variables'][i], 'Reply with only the english translation of the title', False)
    #     tokens += response[1]
    #     data['variables'][i] = response[0].replace('\"', '')
    #     pbar.update(1)
    
    return tokens

def subVars(jaString):
    jaString = jaString.replace('\u3000', ' ')

    # Icons
    count = 0
    iconList = re.findall(r'[\\]+[iI]\[[0-9]+\]', jaString)
    iconList = set(iconList)
    if len(iconList) != 0:
        for icon in iconList:
            jaString = jaString.replace(icon, '<I' + str(count) + '>')
            count += 1

    # Colors
    count = 0
    colorList = re.findall(r'[\\]+[cC]\[[0-9]+\]', jaString)
    colorList = set(colorList)
    if len(colorList) != 0:
        for color in colorList:
            jaString = jaString.replace(color, '<C' + str(count) + '>')
            count += 1

    # Names
    count = 0
    nameList = re.findall(r'[\\]+[nN]\[[0-9]+\]', jaString)
    nameList = set(nameList)
    if len(nameList) != 0:
        for name in nameList:
            jaString = jaString.replace(name, '<N' + str(count) + '>')
            count += 1

    # Variables
    count = 0
    varList = re.findall(r'[\\]+[vV]\[[0-9]+\]', jaString)
    varList = set(varList)
    if len(varList) != 0:
        for var in varList:
            jaString = jaString.replace(var, '<V' + str(count) + '>')
            count += 1

    # Formatting
    count = 0
    formatList = re.findall(r'[\\]+[!.]', jaString)
    formatList = set(formatList)
    if len(formatList) != 0:
        for format in formatList:
            jaString = jaString.replace(format, '<F' + str(count) + '>')
            count += 1

    # Put all lists in list and return
    allList = [iconList, colorList, nameList, varList]
    return [jaString, allList]

def resubVars(translatedText, allList):
    # Fix Spacing and ChatGPT Nonsense
    matchList = re.findall(r'<\s?.+?\s?>', translatedText)
    if len(matchList) > 0:
        for match in matchList:
            text = match.replace(' ', '')
            translatedText = translatedText.replace(match, text)

    # Icons
    count = 0
    if len(allList[0]) != 0:
        for var in allList[0]:
            translatedText = translatedText.replace('<I' + str(count) + '>', var)
            count += 1

    # Colors
    count = 0
    if len(allList[1]) != 0:
        for var in allList[1]:
            translatedText = translatedText.replace('<C' + str(count) + '>', var)
            count += 1

    # Names
    count = 0
    if len(allList[2]) != 0:
        for var in allList[2]:
            translatedText = translatedText.replace('<N' + str(count) + '>', var)
            count += 1

    # Vars
    count = 0
    if len(allList[3]) != 0:
        for var in allList[3]:
            translatedText = translatedText.replace('<V' + str(count) + '>', var)
            count += 1

    # Remove Color Variables Spaces
    # if '\\c' in translatedText:
    #     translatedText = re.sub(r'\s*(\\+c\[[1-9]+\])\s*', r' \1', translatedText)
    #     translatedText = re.sub(r'\s*(\\+c\[0+\])', r'\1', translatedText)
    return translatedText

@retry(exceptions=Exception, tries=5, delay=5)
def translateGPT(t, history, fullPromptFlag):
    # If ESTIMATE is True just count this as an execution and return.
    if ESTIMATE:
        enc = tiktoken.encoding_for_model("gpt-3.5-turbo")
        tokens = len(enc.encode(t)) * 2 + len(enc.encode(history)) + len(enc.encode(PROMPT))
        return (t, tokens)
    
    # Sub Vars
    varResponse = subVars(t)
    subbedT = varResponse[0]

    # If there isn't any Japanese in the text just skip
    if not re.search(r'[一-龠]+|[ぁ-ゔ]+|[ァ-ヴ]+', subbedT):
        return(t, 0)

    """Translate text using GPT"""
    context = 'Eroge Names Context: Name: セレナ == Serena\nGender: Female,\n\nName: カトリーヌ == Catherine\nGender: Female'
    if fullPromptFlag:
        system = PROMPT 
        user = 'Line to Translate: ' + subbedT
    else:
        system = 'You are an expert translator who translates everything to English. Reply with only the English Translation of the text.' 
        user = 'Line to Translate: ' + subbedT
    response = openai.ChatCompletion.create(
        temperature=0,
        frequency_penalty=0.2,
        presence_penalty=0.2,
        model="gpt-3.5-turbo",
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": context},
            {"role": "user", "content": history},
            {"role": "user", "content": user}
        ],
        request_timeout=30,
    )

    # Save Translated Text
    translatedText = response.choices[0].message.content
    tokens = response.usage.total_tokens

    # Resub Vars
    translatedText = resubVars(translatedText, varResponse[1])

    # Remove Placeholder Text
    translatedText = translatedText.replace('English Translation: ', '')
    translatedText = translatedText.replace('Translation: ', '')
    translatedText = translatedText.replace('Line to Translate: ', '')
    translatedText = translatedText.replace('English Translation:', '')
    translatedText = translatedText.replace('Translation:', '')
    translatedText = translatedText.replace('Line to Translate:', '')
    translatedText = re.sub(r'\n\nPast Translated Text:.*', '', translatedText, 0, re.DOTALL)
    translatedText = re.sub(r'Note:.*', '', translatedText)
    translatedText = translatedText.replace('っ', '')

    # Return Translation
    if len(translatedText) > 15 * len(t) or "I'm sorry, but I'm unable to assist with that translation" in translatedText:
        return [t, response.usage.total_tokens]
    else:
        return [translatedText, tokens]