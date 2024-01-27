from configparser import ConfigParser
from pathlib import Path

import requests

config = ConfigParser(delimiters=("="))
config.read(Path(__file__).parent / "config.ini", encoding="utf-8")


def init(server):
    global UserId, api_key
    UserId, api_key = config["accounts"][server].split(",")


class emby_api:
    def __init__(self, server):
        self.server = server
        self.UserId, self.api_key = config["accounts"][server].split(",")

        self.s = requests.Session()
        self.s.headers.update({"Content-Type": "application/json"})

    def ItemService(self, parent_id, params={}):
        """
        params: {"ParentIndexNumber": 1,"Recursive": True, "StartIndex": 2, "Limit": 1}
        pisodeIndex = StartIndex + 1
        https://swagger.emby.media/?staticview=true#/ItemsService/getItems
        series: {'Items': [{'Name': '季 1',
            'ServerId': '51edf6d4c7e145fe86743f1ad3328a4c',
            'Id': '194469',
            'IndexNumber': 1,
            'IsFolder': True,
            'Type': 'Season',
            'UserData': {'UnplayedItemCount': 0,
            'PlaybackPositionTicks': 0,
            'PlayCount': 0,
            'IsFavorite': False,
            'Played': True},
            'SeriesName': '侠探杰克',
            'SeriesId': '194326'}],
            'TotalRecordCount': 1}
        season: {'Items': [{'Name': '欢迎来到马格雷夫',
            'ServerId': '51edf6d4c7e145fe86743f1ad3328a4c',
            'Id': '194470',
            'RunTimeTicks': 32435200000,
            'IndexNumber': 1,
            'ParentIndexNumber': 1,
            'IsFolder': False,
            'Type': 'Episode',
            'UserData': {'PlaybackPositionTicks': 0,
                'PlayCount': 1,
                'IsFavorite': False,
                'LastPlayedDate': '2023-12-18T14:54:02.0000000Z',
                'Played': True},
            'SeriesName': '侠探杰克',
            'SeriesId': '194326',
            'SeasonId': '194469',
            'SeasonName': '季 1',
            'MediaType': 'Video'},...],
            'TotalRecordCount': 8}
        """
        params = {
            "UserId": self.UserId,
            "api_key": self.api_key,
            "ParentId": parent_id,
        } | params
        with self.s.get(f"{self.server}/Items", params=params) as response:
            return response

    def SearchService(self, name, params={}):
        """
        https://swagger.emby.media/?staticview=true#/SearchService/getSearchHints
        {'SearchHints': [{'ItemId': 194326,
          'Id': 194326,
          'Name': '侠探杰克',
          'BackdropImageTag': '8497889743ae09349d749ed0b9a88ef1',
          'BackdropImageItemId': '194326',
          'Type': 'Series',
          'IsFolder': True,
          'RunTimeTicks': 29400000000},
         {'ItemId': 18875,
          'Id': 18875,
          'Name': '侠探杰克',
          'Type': 'Movie',
          'IsFolder': False,
          'RunTimeTicks': 78244160000,
          'MediaType': 'Video'}],
        'TotalRecordCount': 2}
        """
        params = {
            "UserId": self.UserId,
            "api_key": self.api_key,
            "SearchTerm": name,
            "IncludeItemTypes": "Series,Movie",  # https://dev.emby.media/doc/restapi/Item-Types.html
        } | params
        with self.s.get(f"{self.server}/Search/Hints", params=params) as response:
            return response

    def VideoService(self, id, media_source_id, params={}):
        """
        https://swagger.emby.media/?staticview=true#/VideoService/getVideosByIdStream
        https://github.com/MediaBrowser/Emby/wiki/Video-Streaming
        """
        params = {
            "MediaSourceId": media_source_id,
            "PlaySessionId": "fec26290c57444cf98997b08e7ac7366",
            "Static": True,
            "Container": "mkv",
        } | params

        with self.s.get(f"{self.server}/Videos/{id}/stream", params=params) as response:
            return response

    def MediaInfoService(self, id, params={}):
        """
        https://swagger.emby.media/?staticview=true#/MediaInfoService/getVideoStreamInfo
        {'MediaSources': [{'Protocol': 'File',
            'Id': '31388c2c0930dc4ee2c1149c9ba4b59b',
            'Path': '/mnt/emby/us/英美剧集/已完结/2022/侠探杰克 (2022)[tmdbid=108978]/Season 01/Reacher.S01E07.Reacher.Said.Nothing.2160p.AMZN.WEB-DL.DDP5.1.H.265-Lee@CHDWEB.mkv',
            'Container': 'mkv',
            'Size': 4272800542,
            'Name': 'Reacher.S01E07.Reacher.Said.Nothing.2160p.AMZN.WEB-DL.DDP5.1.H.265-Lee@CHDWEB',
            'MediaStreams': [{'Codec': 'hevc',
                'DisplayTitle': '4K HEVC'},
                {'Language': 'eng',
                'DisplayLanguage': 'English',
                'IsDefault': True,
                'Type': 'Subtitle',
                'Index': 2,
                'IsExternal': False}]
            }],
        'PlaySessionId': '1ab'}
        """
        params = {
            "UserId": self.UserId,
            "api_key": self.api_key,
        } | params
        with self.s.get(f"{self.server}/Items/{id}/PlaybackInfo", params=params) as response:
            return response
