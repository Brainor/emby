import configparser
import datetime
import multiprocessing as mp
import re
import time
import urllib.parse as parse
from pathlib import Path
import threading

import fire
import requests


def main(url: str):
    o = parse.urlsplit(url)
    folder_loc = config['settings']['folder']
    file_loc = Path(folder_loc) / get_filename(o, folder_loc)

    # start_loc = file_loc.stat().st_size if file_loc.exists() else 0
    start = time.time()
    print(f'下载: {file_loc.name}')

    # start_event = threading.Event()
    # retry_event = threading.Event()
    progress_events = {k: threading.Event() for k in ['start', 'retry', 'data']}

    progress_dict = {'size': [0] * 10, 'time': [time.time()] * 10, 'index': 0, 'total_length': -1, 'current_length': -2, 'max_speed': 0, 'init_length': -2,
                     'state': 'init'  # state: init, running, slow, success, fail
                     }

    t_progress_bar = threading.Thread(target=progress_bar_3, args=(progress_dict, progress_events), daemon=True)
    t_progress_bar.start()

    (conn_recv, conn_send) = mp.Pipe(False)  # 最大堵塞数受限于mp.connection.BUFSIZE (https://stackoverflow.com/q/58529166/5340217)
    while progress_dict['state'] in ['init', 'fail']:
        progress_dict['state'] = 'running'
        p_download_file = mp.Process(target=emby_download, args=(url, file_loc, conn_send, {'User-Agent', config['settings']['User-Agent']}), daemon=True)
        p_download_file.start()
        while True:  # 循环检索
            if conn_recv.poll():
                data = conn_recv.recv()
                if isinstance(data, dict):
                    if 'current_length' in data and progress_dict['current_length'] == -2:
                        progress_dict['init_length'] = data['current_length']
                    progress_dict.update(data)
                    if progress_dict['current_length'] >= 0 and progress_dict['total_length'] > 0:
                        progress_events['start'].set()  # 开始显示进度条
                elif isinstance(data, int) and data > 0:
                    update_progress_dict(progress_dict, data)
                progress_events['data'].set()  # 有新数据

            match progress_dict['state']:
                case 'running':
                    if not p_download_file.is_alive() and progress_dict['current_length'] != progress_dict['total_length']:
                        print('\n下载失败, 5秒后重新连接')
                        progress_dict['state'] = 'fail'
                        time.sleep(5)
                        break
                case 'fail':
                    p_download_file.terminate()
                    p_download_file.join()
                    print('\n速度太慢, 5秒后重新连接')
                    time.sleep(5)
                    break
                case 'success':
                    break

            time.sleep(1)

    end = time.time()
    t_progress_bar.join()  # 等待progress bar结束
    # print()
    print(f'耗时: {(end - start):.2f}s, 平均速度: {sizeof_fmt((progress_dict["total_length"]-progress_dict["init_length"])/(end - start))}/s\n')


def emby_download(url: str, file_loc: Path, conn_send, info: dict):
    o = parse.urlsplit(url)
    start_loc = file_loc.stat().st_size if file_loc.exists() else 0
    conn_send.send({'current_length': start_loc})

    header = {'Accept': '*/*', 'Accept-Encoding': 'identity;q=1, *;q=0', 'Accept-Language': 'zh-CN,zh;q=0.9', 'Host': o.netloc, 'Connection': 'keep-alive', 'Referer': f'{o.scheme}://{o.netloc}/web/index.html', 'User-Agent': info['User-Agent'], 'Range': f'bytes={start_loc}-'} | {'Sec-Fetch-Dest': 'video', 'Sec-Fetch-Mode': 'no-cors', 'Set-Fetch-Site': 'same-origin'}
    # with s.get(url, headers=header, stream=True, proxies={'http': '127.0.0.1:7890', 'https': '127.0.0.1:7890'}) as response:
    try:
        with s.get(url, headers=header, stream=True) as response:
            response.raise_for_status()

            total_length = response.headers.get('content-length')
            if total_length is None:  # no content length header
                return response.content
            # if speed_dict['total_length'] < 0:
            conn_send.send({'total_length': int(response.headers['Content-Range'].split('/')[1])})

            method_shutil(file_loc, response, conn_send)  # 使用 shutil.copyfileobj, 最快能达8M/s
    except Exception as e:
        print(response.headers)
        raise e
    conn_send.send({'state': 'success'})  # 跑完则为success


def method_shutil(file_loc: Path, response: requests.Response, conn_send):
    length = 16 * 1024 * 1024
    with (open(file_loc, 'ab') as f,  # https://stackoverflow.com/a/29967714/5340217
          memoryview(bytearray(length)) as mv):
        while True:
            n = response.raw.readinto(mv)
            if not n:
                break
            elif n < length:
                with mv[:n] as smv:
                    f.write(smv)

                    conn_send.send(n)

                break
            else:
                f.write(mv)

                conn_send.send(n)


def progress_bar_2(speed_dict):
    from alive_progress import alive_bar
    slow_flag = 0
    with alive_bar(speed_dict['total_length'], force_tty=True, monitor='{count}/{total}', stats='{rate}/s 预计: {eta}', elapsed=False, elapsed_end='耗时: {elapsed}', stats_end='平均速度: {rate}/s') as bar:
        bar(speed_dict['current_length'])
        while True:
            if speed_dict['total_length'] < 0 or speed_dict['current_length'] < 0:
                bar(0)
                time.sleep(1)
            else:
                speed = (sum(speed_dict['size']) / (time.time() - min(speed_dict['time']))) if max(speed_dict['time']) > min(speed_dict['time']) else 0
                speed_dict['max_speed'] = max(speed_dict['max_speed'], speed)
                if speed <= speed_dict['max_speed'] / 2 and speed < 1024 * 1024:  # 包括开始时速度为0的情况, 速度<1M/s才考虑重置
                    slow_flag += 1
                else:
                    slow_flag = 0
                if slow_flag >= 30:  # 超过30秒速度<200kB/s
                    speed_dict['state'] = 'slow'
                    break
                done = int(50 * speed_dict['current_length'] / speed_dict['total_length'])  # https://stackoverflow.com/a/21868231/5340217
                est = int((speed_dict['total_length'] - speed_dict['current_length']) / speed) if speed else -1
                # print(f'\r[{"=" * done}{" " * (50-done)}] {sizeof_fmt(speed)}/s {sizeof_fmt(speed_dict["current_length"])}/{sizeof_fmt(speed_dict["total_length"])} 预计: {str(datetime.timedelta(seconds=est))}', end='')
                time.sleep(1)
                bar(speed_dict['current_length'] / speed_dict['total_length'])


def progress_bar_3(progress_dict, progress_events):
    from alive_progress import alive_bar
    progress_events['start'].wait()  # 获得了total_length才开始
    progress_events['start'].clear()
    speed_max = 0
    slow_flag = 0
    # progress_events['start'].clear()
    with alive_bar(progress_dict['total_length'], force_tty=True, monitor='{count}/{total}', stats='{rate}/s 预计: {eta}', elapsed=False, elapsed_end='耗时: {elapsed}', stats_end='平均速度: {rate}/s') as bar:  # 开始显示进度条
        while progress_dict['state'] != 'success':  # 只要不成功一定会跑进度条
            if progress_events['data'].is_set():  # 有数据
                progress_events['data'].clear()
                if progress_dict['current_length'] > bar.current():
                    bar(progress_dict['current_length'] - bar.current())

            speed = bar.speed()
            speed_max = max(speed_max, speed)
            if speed <= speed_max / 2 and speed < 1024 * 1024:
                slow_flag += 1
            else:
                slow_flag = 0
            if slow_flag >= 30:  # 超过30秒速度<200kB/s
                progress_dict['state'] = 'fail'
                slow_flag = 0
                progress_events['start'].wait()  # 等待下载程序重新开始
                progress_events['start'].clear()
            time.sleep(1)


def update_progress_dict(progress_dict, n):
    progress_dict['size'][progress_dict['index'] % 10] = n
    progress_dict['time'][progress_dict['index'] % 10] = time.time()
    progress_dict['index'] += 1
    progress_dict['current_length'] += n


def sizeof_fmt(num, suffix="B"):  # https://stackoverflow.com/a/1094933/5340217
    for unit in ["", "k", "M", "G", "T", "P", "E", "Z"]:
        if abs(num) < 1024.0:
            return f"{num:3.2f}{unit}{suffix}"
        num /= 1024.0
    return f"{num:.2f}Y{suffix}"


def get_filename(o: parse.SplitResult, folder_loc: str):
    path_list = o.path.split('/')
    url = parse.urlunsplit([o.scheme, o.netloc, '/'.join(path_list[:2] + ['Users', get_userID(o), 'Items'] + [path_list[-2]]), f'X-Emby-Token={parse.parse_qs(o.query)["api_key"][0]}', ''])
    header = {'Connection': 'keep-alive', 'accept': 'application/json', 'Sec-Fetch-Dest': 'empty', 'User-Agent': config['settings']['User-Agent'], 'Sec-Fetch-Site': 'same-origin', 'Sec-Fetch-Mode': 'cors', 'Referer': f'{o.scheme}://{o.netloc}/web/index.html', 'Accept-Language': 'zh-CN,zh;q=0.9'}
    try:
        with s.get(url, headers=header) as response:
            response_json = response.json()
    except:
        print(f'获取文件名失败: {url}\n{response.text}')
        return "stream.mkv"
    if 'SeriesName' in response_json:
        # season = re.findall(r'\d+', response_json['SeasonName'])[0]
        # episode = re.findall(r'\d+', response_json['SortName'])[0]
        season = response_json['ParentIndexNumber']
        episode = response_json['IndexNumber']
        filename = f"{response_json['SeriesName']}.S{season:02}E{episode:02}.{response_json['Container']}"
    else:
        filename = f"{response_json['Name']}.{response_json['Container']}"

    # 顺便下载字幕
    showID = path_list[3]
    mediaID = parse.parse_qs(o.query)['MediaSourceId'][0]
    for mediaSource in response_json['MediaSources']:
        if mediaSource['Id'] == mediaID:
            mediaStreams = mediaSource['MediaStreams']
            break
    subtitle_index = {'ass': -1, 'srt': -1}
    for mediaStream in mediaStreams:
        if mediaStream['IsExternal']:
            codec = mediaStream['Codec']
            if codec not in subtitle_index.keys():
                print(f'未知字幕格式: {codec}')
                codec = 'other'
                continue
            subtitle_index[codec] = max(subtitle_index[codec], mediaStream['Index'])
    for codec, index in subtitle_index.items():
        if index > -1:
            url = parse.urlunsplit([o.scheme, o.netloc, '/'.join(path_list[:2] + ['Videos', showID, mediaID, 'Subtitles', str(index), f'Stream.{codec}']), '', ''])
            print(url)
            with open(Path(folder_loc) / filename.replace(response_json['Container'], codec), 'wb') as f:
                f.write(s.get(url, headers=header).content)
            break

    return filename


def get_userID(o):
    # 在*.emby/Users/*这里查看
    # emby/Users/authenticatebyname response['User']['Id']

    key = re.sub(r':.*$', '', o.netloc)
    if config.has_option('account', key):
        return config['account'][key]
    else:
        print(f'访问{o.netloc}并重新运行')
        username = input('username:')
        password = input('password:')
        header = {'Connection': 'keep-alive', 'accept': 'application/json', 'User-Agent': config['settings']['User-Agent'], 'Sec-Fetch-Site': 'same-origin', 'Sec-Fetch-Mode': 'cors', 'Referer': f'{o.scheme}://{o.netloc}/web/index.html', 'Accept-Language': 'zh-CN,zh;q=0.9'}
        url = parse.urlunsplit([o.scheme, o.netloc, 'emby/Users/authenticatebyname', f'X-Emby-Client=Emby Web&X-Emby-Device-Name=Chrome&X-Emby-Device-Id={config["settings"]["X-Emby-Device-Id"]}&X-Emby-Client-Version=4.6.7.0', ''])
        response = s.post(url, json={'Username': username, 'Pw': password}, headers=header).json()
        ID = response['User']['Id']
        print(ID)
        print(f'api_key={response["AccessToken"]}')
        config['account'][key] = ID
        with open('config.ini', 'w') as configfile:
            config.write(configfile)
        exit()


def progress_bar(speed_dict):
    slow_flag = 0
    while True:
        if speed_dict['total_length'] < 0 or speed_dict['current_length'] < 0:
            time.sleep(1)
        else:
            speed = (sum(speed_dict['size']) / (time.time() - min(speed_dict['time']))) if max(speed_dict['time']) > min(speed_dict['time']) else 0
            speed_dict['max_speed'] = max(speed_dict['max_speed'], speed)
            if speed <= speed_dict['max_speed'] / 2 and speed < 1024 * 1024:  # 包括开始时速度为0的情况, 速度<1M/s才考虑重置
                slow_flag += 1
            else:
                slow_flag = 0
            if slow_flag >= 30:  # 超过30秒速度<200kB/s
                speed_dict['state'] = True
                break
            done = int(50 * speed_dict['current_length'] / speed_dict['total_length'])  # https://stackoverflow.com/a/21868231/5340217
            est = int((speed_dict['total_length'] - speed_dict['current_length']) / speed) if speed else -1
            print(f'\r[{"=" * done}{" " * (50-done)}] {sizeof_fmt(speed)}/s {sizeof_fmt(speed_dict["current_length"])}/{sizeof_fmt(speed_dict["total_length"])} 预计: {str(datetime.timedelta(seconds=est))}', end='')
            time.sleep(1)


def monitor():
    config.read('config.ini')
    if not config.has_option('settings', 'folder'):
        print('no folder location in settings')
        exit()
    if not config.has_option('settings', 'X-Emby-Device-Id') is None:
        print('need device ID info in settings')
        exit()
    if not config.has_option('settings', 'User-Agent') is None:
        print('need user-agent info in settings')
        exit()

    while True:
        with open('emby_links.txt', 'r') as f:
            urls = [url for url in f.readlines() if len(url.strip()) > 0]
        if len(urls):
            url = urls[0]
            main(url.strip())  # 包含了\n
            with open('emby_links.txt', 'r') as f:
                urls = f.readlines()  # 有可能在下载过程中更新了文件
            if urls[0] == url:
                urls = [i for i in urls if i != url]
                with open('emby_links.txt', 'w') as f:
                    f.writelines(urls)
            else:
                print('url changed')
                break

        time.sleep(1)


def add_list(url: str):
    print(url.strip())
    with open('emby_links.txt', 'a') as f:
        f.write(url.strip() + '\n')


def modify_alive_bar():
    import inspect

    import alive_progress.core.progress

    source = inspect.getsource(alive_progress.core.progress)
    new_source = source.replace('count=run.count', 'count=sizeof_fmt(run.count)').replace('total=total', 'total=sizeof_fmt(total)').replace('rate=run.rate, rate_spec=rate_spec,', 'rate=sizeof_fmt(run.rate), rate_spec=rate_spec,').replace('(rate=run.rate, rate_spec=rate_spec)', '(rate=sizeof_fmt(run.rate), rate_spec=rate_spec)')

    sizeof_fmt_source = inspect.getsource(sizeof_fmt)
    new_source = new_source + '\n' + sizeof_fmt_source

    # 更新rate计算公式, expose rate
    new_source = new_source.replace('(pause_monitoring, current, set_title, set_text)', '(pause_monitoring, current, set_title, set_text)\n    bar.speed = lambda:run.rate\n')

    exec(new_source, alive_progress.core.progress.__dict__)


def check_server(para=None):
    if para:
        add_list(para)
    else:
        modify_alive_bar()
        monitor()


if __name__ == '__main__':
    s = requests.Session()
    config = configparser.ConfigParser()  # or xaml?

    fire.Fire(check_server)
