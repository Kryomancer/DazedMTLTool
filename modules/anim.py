# Libraries
import json, os, re, textwrap, threading, time, traceback, tiktoken, openai
from pathlib import Path
from colorama import Fore
from dotenv import load_dotenv
from retry import retry
from tqdm import tqdm

# Open AI
load_dotenv()
if os.getenv('api').replace(' ', '') != '':
    openai.api_base = os.getenv('api')
openai.organization = os.getenv('org')
openai.api_key = os.getenv('key')

#Globals
MODEL = os.getenv('model')
TIMEOUT = int(os.getenv('timeout'))
LANGUAGE = os.getenv('language').capitalize()
PROMPT = Path('prompt.txt').read_text(encoding='utf-8')
THREADS = int(os.getenv('threads'))
LOCK = threading.Lock()
WIDTH = int(os.getenv('width'))
LISTWIDTH = int(os.getenv('listWidth'))
NOTEWIDTH = 70
MAXHISTORY = 10
ESTIMATE = ''
TOKENS = [0, 0]
NAMESLIST = []
NAMES = False    # Output a list of all the character names found
BRFLAG = False   # If the game uses <br> instead
FIXTEXTWRAP = True  # Overwrites textwrap
IGNORETLTEXT = False    # Ignores all translated text.
MISMATCH = []   # Lists files that throw a mismatch error (Length of GPT list response is wrong)

#tqdm Globals
BAR_FORMAT='{l_bar}{bar:10}{r_bar}{bar:-10b}'
POSITION = 0
LEAVE = False

# Pricing - Depends on the model https://openai.com/pricing
# Batch Size - GPT 3.5 Struggles past 15 lines per request. GPT4 struggles past 50 lines per request
# If you are getting a MISMATCH LENGTH error, lower the batch size.
if 'gpt-3.5' in MODEL:
    INPUTAPICOST = .002 
    OUTPUTAPICOST = .002
    BATCHSIZE = 10
elif 'gpt-4' in MODEL:
    INPUTAPICOST = .01
    OUTPUTAPICOST = .03
    BATCHSIZE = 50  

def handleAnim(filename, estimate):
    global ESTIMATE
    totalTokens = [0,0]
    ESTIMATE = estimate

    if estimate:
        start = time.time()
        translatedData = openFiles(filename)

        # Print Result
        end = time.time()
        tqdm.write(getResultString(translatedData, end - start, filename))
        with LOCK:
            totalTokens[0] += translatedData[1][0]
            totalTokens[1] += translatedData[1][1]

        # Print Total
        totalString = getResultString(['', totalTokens, None], end - start, 'TOTAL')

        # Print any errors on maps
        if len(MISMATCH) > 0:
            return totalString + Fore.RED + f'\nMismatch Errors: {MISMATCH}' + Fore.RESET
        else:
            return totalString
    
    else:
        try:
            with open('translated/' + filename, 'w', encoding='UTF-8') as outFile:
                start = time.time()
                translatedData = openFiles(filename)

                # Print Result
                end = time.time()
                json.dump(translatedData[0], outFile, ensure_ascii=False)
                tqdm.write(getResultString(translatedData, end - start, filename))
                with LOCK:
                    totalTokens[0] += translatedData[1][0]
                    totalTokens[1] += translatedData[1][1]
        except Exception as e:
            return 'Fail'

    return getResultString(['', totalTokens, None], end - start, 'TOTAL')

def openFiles(filename):
    with open('files/' + filename, 'r', encoding='UTF-8-sig') as f:
        data = json.load(f)

        # Map Files
        if '.json' in filename:
            translatedData = parseJSON(data, filename)

        else:
            raise NameError(filename + ' Not Supported')
    
    return translatedData

def getResultString(translatedData, translationTime, filename):
    # File Print String
    totalTokenstring =\
        Fore.YELLOW +\
        '[Input: ' + str(translatedData[1][0]) + ']'\
        '[Output: ' + str(translatedData[1][1]) + ']'\
        '[Cost: ${:,.4f}'.format((translatedData[1][0] * .001 * INPUTAPICOST) +\
        (translatedData[1][1] * .001 * OUTPUTAPICOST)) + ']'
    timeString = Fore.BLUE + '[' + str(round(translationTime, 1)) + 's]'

    if translatedData[2] == None:
        # Success
        return filename + ': ' + totalTokenstring + timeString + Fore.GREEN + u' \u2713 ' + Fore.RESET

    else:
        # Fail
        try:
            raise translatedData[2]
        except Exception as e:
            traceback.print_exc()
            errorString = str(e) + Fore.RED
            return filename + ': ' + totalTokenstring + timeString + Fore.RED + u' \u2717 ' +\
                errorString + Fore.RESET
        
def parseJSON(data, filename):
    keys = list(data.keys())
    batches = [keys[i:i + BATCHSIZE] for i in range(0, len(keys), BATCHSIZE)]
    totalTokens = [0, 0]
    totalLines = 0
    totalLines = len(batches)
    global LOCK
    
    with tqdm(bar_format=BAR_FORMAT, position=POSITION, total=totalLines, leave=LEAVE) as pbar:
        pbar.desc=filename
        pbar.total=totalLines
        try:
            result = translateJSON(batches, data, pbar)
            totalTokens[0] += result[0]
            totalTokens[1] += result[1]
        except Exception as e:
            traceback.print_exc()
            return [data, totalTokens, e]
    return [data, totalTokens, None]

def translateJSON(keys, data, pbar):
    translatedBatch = []
    textHistory = []
    tokens = [0, 0]

    for batch in keys:
        # Save Batch
        originalBatch = batch.copy()

        # If there isn't any Japanese in the text just skip
        needTL = False
        for i in range(len(batch)):
            t = data[batch[i]]
            if re.search(r'[一-龠ぁ-ゔァ-ヴーａ-ｚＡ-Ｚ０-９]+', t) or t == '':
                needTL = True
        if needTL is False and IGNORETLTEXT is True:
            pbar.update(1)
            continue            

        # Remove any textwrap and Furigana
        for i in range(len(batch)):
            if FIXTEXTWRAP == True:
                # Textwrap
                data[originalBatch[i]] = data[originalBatch[i]].replace('@b', ' ')

            # Furigana
            rcodeMatch = re.findall(r'(@\[(.+?):.+?\])', batch[i])
            if len(rcodeMatch) > 0:
                for match in rcodeMatch:
                    batch[i] = batch[i].replace(match[0], match[1])

        # Translate
        if needTL is True:
            response = translateGPT(batch, textHistory, True)
            tokens[0] += response[1][0]
            tokens[1] += response[1][1]
            translatedBatch = response[0]
        else:
            for i in range(len(originalBatch)):
                translatedBatch.append(data[originalBatch[i]])

        # Format and Set Text
        if len(batch) == len(translatedBatch):
            for i in range(len(translatedBatch)):

                # Remove added speaker
                translatedText = translatedBatch[i]
                translatedText = re.sub(r'^.+?\s\|\s?', '', translatedText)

                # Textwrap
                if '@b' not in translatedText:
                    translatedText = textwrap.fill(translatedText, width=WIDTH)
                    translatedText = translatedText.replace('\n', '@b')

                # Set Data
                data[originalBatch[i]] = translatedText
                textHistory = translatedBatch
            translatedBatch.clear()
        # Mismatch, Skip Batch
        else:
            MISMATCH.append(batch)
            pbar.update(1)
            continue
        pbar.update(1)

    return tokens  

def subVars(jaString):
    jaString = jaString.replace('\u3000', ' ')

    # Nested
    count = 0
    nestedList = re.findall(r'[\\]+[\w]+\[[\\]+[\w]+\[[0-9]+\]\]', jaString)
    nestedList = set(nestedList)
    if len(nestedList) != 0:
        for icon in nestedList:
            jaString = jaString.replace(icon, '{Nested_' + str(count) + '}')
            count += 1

    # Icons
    count = 0
    iconList = re.findall(r'[\\]+[iIkKwWaA]+\[[0-9]+\]', jaString)
    iconList = set(iconList)
    if len(iconList) != 0:
        for icon in iconList:
            jaString = jaString.replace(icon, '{Ascii_' + str(count) + '}')
            count += 1

    # Colors
    count = 0
    colorList = re.findall(r'[\\]+[cC]\[[0-9]+\]', jaString)
    colorList = set(colorList)
    if len(colorList) != 0:
        for color in colorList:
            jaString = jaString.replace(color, '{Color_' + str(count) + '}')
            count += 1

    # Names
    count = 0
    nameList = re.findall(r'[\\]+[nN]\[.+?\]+', jaString)
    nameList = set(nameList)
    if len(nameList) != 0:
        for name in nameList:
            jaString = jaString.replace(name, '{Noun_' + str(count) + '}')
            count += 1

    # Variables
    count = 0
    varList = re.findall(r'[\\]+[vV]\[[0-9]+\]', jaString)
    varList = set(varList)
    if len(varList) != 0:
        for var in varList:
            jaString = jaString.replace(var, '{Var_' + str(count) + '}')
            count += 1

    # Formatting
    count = 0
    formatList = re.findall(r'[\\]+[\w]+\[.+?\]', jaString)
    formatList = set(formatList)
    if len(formatList) != 0:
        for var in formatList:
            jaString = jaString.replace(var, '{FCode_' + str(count) + '}')
            count += 1

    # Put all lists in list and return
    allList = [nestedList, iconList, colorList, nameList, varList, formatList]
    return [jaString, allList]

def resubVars(translatedText, allList):
    # Fix Spacing and ChatGPT Nonsense
    matchList = re.findall(r'\[\s?.+?\s?\]', translatedText)
    if len(matchList) > 0:
        for match in matchList:
            text = match.strip()
            translatedText = translatedText.replace(match, text)

    # Nested
    count = 0
    if len(allList[0]) != 0:
        for var in allList[0]:
            translatedText = translatedText.replace('{Nested_' + str(count) + '}', var)
            count += 1

    # Icons
    count = 0
    if len(allList[1]) != 0:
        for var in allList[1]:
            translatedText = translatedText.replace('{Ascii_' + str(count) + '}', var)
            count += 1

    # Colors
    count = 0
    if len(allList[2]) != 0:
        for var in allList[2]:
            translatedText = translatedText.replace('{Color_' + str(count) + '}', var)
            count += 1

    # Names
    count = 0
    if len(allList[3]) != 0:
        for var in allList[3]:
            translatedText = translatedText.replace('{Noun_' + str(count) + '}', var)
            count += 1

    # Vars
    count = 0
    if len(allList[4]) != 0:
        for var in allList[4]:
            translatedText = translatedText.replace('{Var_' + str(count) + '}', var)
            count += 1
    
    # Formatting
    count = 0
    if len(allList[5]) != 0:
        for var in allList[5]:
            translatedText = translatedText.replace('{FCode_' + str(count) + '}', var)
            count += 1

    return translatedText

def batchList(input_list, batch_size):
    if not isinstance(batch_size, int) or batch_size <= 0:
        raise ValueError("batch_size must be a positive integer")
        
    return [input_list[i:i + batch_size] for i in range(0, len(input_list), batch_size)]

def createContext(fullPromptFlag, subbedT):
    characters = 'Game Characters:\
        篠崎 誠一 == Shinozaki Seiichi - Male\
        宮前 遥奈 == Miyamae Haruna - Female\
        榛名 悠真 == Haruna Yuuma - Male\
        浪川 時宗 == Namikawa Tokimune - Male\
        高嶋 美雪 == Takashima Miyuki - Female'
    system = PROMPT if fullPromptFlag else \
        f'Output ONLY the {LANGUAGE} translation in the following format: `Translation: <{LANGUAGE.upper()}_TRANSLATION>`'
    user = f'{subbedT}'
    return characters, system, user

def translateText(characters, system, user, history):
    # Prompt
    msg = [{"role": "system", "content": system}]

    # Characters
    msg.append({"role": "user", "content": characters})

    # History
    if isinstance(history, list):
        msg.extend([{"role": "user", "content": h} for h in history])
    else:
        msg.append({"role": "user", "content": history})
    
    # Content to TL
    msg.append({"role": "user", "content": user})
    response = openai.ChatCompletion.create(
        temperature=0,
        frequency_penalty=0,
        presence_penalty=0,
        model=MODEL,
        messages=msg,
        request_timeout=TIMEOUT,
    )
    return response

def cleanTranslatedText(translatedText, varResponse):
    placeholders = {
        f'{LANGUAGE} Translation: ': '',
        'Translation: ': '',
        'っ': '',
        # Add more replacements as needed
    }
    for target, replacement in placeholders.items():
        translatedText = translatedText.replace(target, replacement)

    translatedText = resubVars(translatedText, varResponse[1])
    return [line for line in translatedText.split('\n') if line]

def extractTranslation(translatedTextList, is_list):
    pattern = r'<Line(\d+)>(.*)</Line\d+>'
    # If it's a batch (i.e., list), extract with tags; otherwise, return the single item.
    if is_list:
        return [re.findall(pattern, line)[0][1] for line in translatedTextList if re.search(pattern, line)]
    else:
        matchList = re.findall(pattern, translatedTextList)
        return matchList[0][1] if matchList else translatedTextList

def countTokens(characters, system, user, history):
    inputTotalTokens = 0
    outputTotalTokens = 0
    enc = tiktoken.encoding_for_model(MODEL)
    
    # Input
    if isinstance(history, list):
        for line in history:
            inputTotalTokens += len(enc.encode(line))
    else:
        inputTotalTokens += len(enc.encode(history))
    inputTotalTokens += len(enc.encode(system))
    inputTotalTokens += len(enc.encode(characters))
    inputTotalTokens += len(enc.encode(user))

    # Output
    outputTotalTokens += round(len(enc.encode(user))/1.7)

    return [inputTotalTokens, outputTotalTokens]

def combineList(tlist, text):
    if isinstance(text, list):
        return [t for sublist in tlist for t in sublist]
    return tlist[0]

@retry(exceptions=Exception, tries=5, delay=5)
def translateGPT(text, history, fullPromptFlag):
    totalTokens = [0, 0]
    if isinstance(text, list):
        tList = batchList(text, BATCHSIZE)
    else:
        tList = [text]

    for index, tItem in enumerate(tList):
        # Before sending to translation, if we have a list of items, add the formatting
        if isinstance(tItem, list):
            payload = '\n'.join([f'<Line{i}>{item}</Line{i}>' for i, item in enumerate(tItem)])
            varResponse = subVars(payload)
            subbedT = varResponse[0]
        else:
            varResponse = subVars(tItem)
            subbedT = varResponse[0]

        # Things to Check before starting translation
        if not re.search(r'[一-龠ぁ-ゔァ-ヴーａ-ｚＡ-Ｚ０-９]+', subbedT):
            continue

        # Create Message
        characters, system, user = createContext(fullPromptFlag, subbedT)

        # Calculate Estimate
        if ESTIMATE:
            estimate = countTokens(characters, system, user, history)
            totalTokens[0] += estimate[0]
            totalTokens[1] += estimate[1]
            continue

        # Translating
        response = translateText(characters, system, user, history)
        translatedText = response.choices[0].message.content
        totalTokens[0] += response.usage.prompt_tokens
        totalTokens[1] += response.usage.completion_tokens

        # Formatting
        translatedTextList = cleanTranslatedText(translatedText, varResponse)
        if isinstance(tItem, list):
            extractedTranslations = extractTranslation(translatedTextList, True)
            tList[index] = extractedTranslations
            history = extractedTranslations[-10:]  # Update history if we have a list
        else:
            # Ensure we're passing a single string to extractTranslation
            extractedTranslations = extractTranslation('\n'.join(translatedTextList), False)
            tList[index] = extractedTranslations

    finalList = combineList(tList, text)
    return [finalList, totalTokens]