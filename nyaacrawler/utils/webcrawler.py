from nyaacrawler.models import Anime, Torrent, AnimeAlias
from nyaacrawler.utils import emailSender
from django.conf import settings

from bs4 import BeautifulSoup
from PIL import Image, ImageOps

from hashlib import sha1
import bencode
import datetime

import urllib
import urllib2
import re
import sys
import time
import os

import logging
logger = logging.getLogger(__name__)

#url parameters - subject to change
BASE_URL = 'http://www.nyaa.se/'
ENGLISH_TRANSLATED = '1_37'
TRUSTED_ONLY = 2
DEFAULT_RSS_DATE_FORMAT = '%a, %d %b %Y %H:%M:%S +0000'
ROWS_PER_PAGE = 100

INITIAL_CRAWL = 'init'
INCREMENTAL_CRAWL = 'incr'

def torrent_arrived(torrent):
    """
    Gets all matching subscribers and send notification email
    """

    matched_subscriptions = torrent.get_matching_subscriptions()
    
    for subscription in matched_subscriptions:
        subscription_parameters = {}
        subscription_parameters['episode'] = str(torrent.episode)
        subscription_parameters['anime'] = torrent.title.anime.official_title
        subscription_parameters['email'] = subscription.get_email()
        subscription_parameters['unsubscribe_key'] = subscription.get_unsubscribe_key()
        subscription_parameters['torrent_url'] = torrent.url

        try:
            emailSender.send_notification_email (subscription_parameters)
        except:
            logger.error ( str(sys.exc_info()) )

        subscription.increment_episode()
        subscription.save()

def get_title_regex_string():
    #example: [fansub group] anime name - 05 [720p].mkv
    rStr = ''
    # fansub group
    rStr += '(?:\[(.+?)\])?'
    # title
    rStr += '\s(.+?)\s'
    # title/episode separator (assumes that everything is in English)
    rStr += '\-(?=[^A-Za-z]{2}).*?'
    # episode #
    rStr += '0?(\d+).*?'
    # quality
    rStr += '(?:.*?((?:\d+(?:p|P))|(?:\d+x\d+)).*?)?'
    # format
    rStr += '(?=(mkv|mp4|avi))'

    return rStr

def get_meta_regex_string():
    
    rStr = ''
    # seeders
    rStr += '(\d+)\sseeder\(s\),\s'
    # leechers
    rStr += '(\d+)\sleecher\(s\),\s'
    # downloads (skipped)
    rStr += '\d+\sdownload\(s\)\s-\s'
    # size
    rStr += '(\d+(?:\.\d+)?\s[MG]iB)'
    
    return rStr
    
    
def get_torrent_info_hash(torrent_link):
    """
    Gets the info hash from a torrent file
    """
    rawdata = urllib2.urlopen(torrent_link).read();

    metainfo = bencode.bdecode(rawdata)

    obj = sha1(bencode.bencode(metainfo['info']) )

    return obj.hexdigest()

def crawl_anime():
    """
    Scapes the front page of nyaa:
    an incremental crawl of torrents from nyaa.se
    """
    continue_crawl = True
    offset = 1
    
    rss_datetime_file = open(settings.RSS_FETCH_DATETIME_PATH, 'r+')
    rss_datetime = rss_datetime_file.read().strip()
    last_crawled_time = time.strptime(rss_datetime, DEFAULT_RSS_DATE_FORMAT)
    rss_datetime_file.close()

    while (continue_crawl):
        
        #url to crawl
        query_parameters = {
            'page' : 'rss',
            'cats' : ENGLISH_TRANSLATED,
            'filter' : TRUSTED_ONLY,
            'offset' : offset
        }

        url = BASE_URL + '?' + urllib.urlencode(query_parameters)
        continue_crawl = crawl_page(url, INCREMENTAL_CRAWL, last_crawled_time)

        offset += 1

def crawl_specific_anime(anime_name):
    """
    Scrapes an anime using the search in nyaa.se
    """
    continue_crawl = True
    offset = 1

    while continue_crawl:

        query_parameters = {
            'page' : 'rss',
            'cats' : ENGLISH_TRANSLATED,
            'filter' : TRUSTED_ONLY,
            'term' : anime_name.encode('utf-8'),
            'offset' : offset
        }

        url = BASE_URL +'?'+ urllib.urlencode(query_parameters)
        continue_crawl = crawl_page(url, INITIAL_CRAWL)

        offset += 1

def filter_quality(quality):
    if quality == "480p" or quality == "848x480":
        return "480p"
    elif quality == "480p" or quality == "1920x1080":
        return "1080p"
    else:
        # quality is usually 720p if no quality is specified
        return "720p"

def parse_row(title_regex, meta_regex, item):
    try:
        torrent_name = item.title.text.replace('_', ' ')
        url = item.guid.text
        torrent_link = item.link.text
        meta = item.description.text
        
        # extract data after some normalization
        res = title_regex.match(torrent_name)
        meta_res = meta_regex.match(meta)

        if not res or not meta_res:
            return

        #get torrent info
        fansub = res.group(1)
        animeName = res.group(2)
        episode = res.group(3)
        quality = filter_quality( format(res.group(4)) )

        vidFormat = format(res.group(5))
        
        seeders = meta_res.group(1)
        leechers = meta_res.group(2)
        file_size = meta_res.group(3)

        #A new alias name is stored if it has not been detected yet
        
        anime_alias_obj, created = AnimeAlias.objects.get_or_create(
            title = animeName,
            defaults = {
                'anime' : Anime.get_unknown_placeholder()
            }
        )

        if (created):
            logger.info("new alias name for unknown anime: " + anime_alias_obj.title + "has been added.")

        animeObj = anime_alias_obj.anime;

        if animeObj.official_title != Anime.UNKNOWN_ANIME:

            if (Torrent.objects.filter(url=url).exists()):
                logger.info ("  torrent already exist: " + torrent_name)
            else:
                info_hash = get_torrent_info_hash(torrent_link)
                torrentObj = Torrent.objects.create(
                        url           =   url,
                        torrent_name  =   torrent_name,
                        title         =   anime_alias_obj,
                        episode       =   episode,
                        fansub        =   fansub,
                        quality       =   quality,
                        vidFormat     =   vidFormat,
                        seeders       =   seeders,
                        leechers      =   leechers,
                        file_size     =   file_size,
                        infoHash      =   info_hash
                )
                
                logger.info ("torrent for " + str(torrentObj) + ": " + torrent_name +" added")
                torrent_arrived(torrentObj)

        else:
           logger.info ("   alias for unknown anime: " + anime_alias_obj.title + " skipped")

    except:
        logger.error('Error at: ' + item.get_text())
        logger.error('with error: ' + str(sys.exc_info()) + '\n')

def crawl_page(url, crawl_type, stop_at=None):
    """
    Scapes a specific nyaa.se page
    returns the number of rows
    """
    continue_crawl = True
    
    logger.info ("Scraping page... " + url)

    title_regex = re.compile(get_title_regex_string())
    meta_regex = re.compile(get_meta_regex_string())

    c=urllib2.urlopen(url)

    soup=BeautifulSoup(c.read(), 'xml')

    record_list = soup.find_all('item')
    num_rows = len(record_list)

    if crawl_type == INITIAL_CRAWL:
        
        for item in record_list:
            parse_row(title_regex, meta_regex, item)

    else: #INCREMENTAL_CRAWL
        logger.info ("starting incremental crawl ...")
        assert (stop_at is not None)

        #update the latest time crawled
        first_item = record_list[0]
        latest_time_added_string = first_item.pubDate.text
        
        rss_datetime_file = open(settings.RSS_FETCH_DATETIME_PATH, 'r+')
        rss_datetime_file_time = time.strptime(rss_datetime_file.read().strip(), DEFAULT_RSS_DATE_FORMAT)
        rss_datetime_file.close()

        if (time.strptime(latest_time_added_string, DEFAULT_RSS_DATE_FORMAT) > rss_datetime_file_time):
            rss_datetime_file = open(settings.RSS_FETCH_DATETIME_PATH, 'w')
            rss_datetime_file.write(latest_time_added_string)
            rss_datetime_file.close()
    
        for item in record_list:
            time_added_string = item.pubDate.text
            time_added = time.strptime(time_added_string, DEFAULT_RSS_DATE_FORMAT)

            #if the item's time if before the last crawled time, then stop
            if (time_added <= stop_at):
                continue_crawl = False
                break

            logger.info ("parsing... "+ item.title.text)
            parse_row(title_regex, meta_regex, item)
        
    continue_crawl = continue_crawl and num_rows == ROWS_PER_PAGE

    logger.info ('Crawl completed')

    return continue_crawl
    
def create_new_season_list(season=""):
    """
    Scrapes seasonal anime chart for new Anime
    Deletes finished airing anime from previous season
    """
    CHART_BASE_URL = "http://anichart.net/"
    
    if season not in ["spring", "summer", "fall", "winter"]:
        month = int(datetime.datetime.now().strftime("%m"))

        if month >= 3 and month < 6:
            season = "spring"
        elif month >= 6 and month < 9:
            season = "summer"
        elif month >= 9 and month < 12:
            season = "fall"
        else:
            season = "winter"
    
    c=urllib2.urlopen(CHART_BASE_URL + season)
    soup=BeautifulSoup(c.read())

    anime_list = soup.find_all("div", "anime_info")

    # deletes finished anime from previous season 

    previous_anime_list = [anime.official_title for anime in Anime.objects.all()]
    current_anime_list = []
    for anime in anime_list:
        title = anime.find("div", "title").text.strip()
        current_anime_list.append(title)

    # the 'unknown anime object' must always exist
    current_anime_list.append(Anime.UNKNOWN_ANIME)

    finished_anime_list = list( set(previous_anime_list) - set(current_anime_list))

    for anime_title in finished_anime_list:
        Anime.objects.get(official_title=anime_title).delete()  

    # adds new anime from current season
    for anime in anime_list:
        title = anime.find("div", "title").text.strip()
        img_src = anime.find("img", "thumb").get("src")
        if "../" in img_src:
            img_src = img_src.replace("../", CHART_BASE_URL)

        filename = img_src.split("/")[-1]
        image_outpath = os.path.join (settings.STATIC_ROOT + "/nyaacrawler/anime_images/",  filename)

        if (not os.path.isfile(image_outpath)):
            # arbitrary user agent
            user_agent = "Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:26.0) Gecko/20100101 Firefox/26.0"
            header = { 'User-Agent' : user_agent }

            # write images 
            req = urllib2.Request(img_src, None, header)
            resp = urllib2.urlopen(req)
            f = open(image_outpath,'w')
            f.write( resp.read() )
            f.close()
            
            logger.info ("Wrote: " + image_outpath )

            # Resize images
            image = Image.open( image_outpath )
            imageresize = image.resize( (125,163), Image.ANTIALIAS)
            imageresize.save(image_outpath, 'JPEG')

        if (AnimeAlias.objects.filter(title=title).exists()):
            existing_alias = AnimeAlias.objects.get(title=title)
            #the existing alias points to the 'UNKNOWN-ANIME'
            #create a new anime and link the existing alias to it
            if (existing_alias.anime.official_title == Anime.UNKNOWN_ANIME):
                anime_obj = Anime.objects.create(official_title=title, image=filename)
                existing_alias.anime = anime_obj
                existing_alias.save()
                logger.info("Anime: " + title + " parent modified")
            else:
                logger.info("Anime: " + title + " already exist in database!")

        else:
            #The alias name does not exist - create an anime object and set its alias to the given title.
            anime_obj = Anime.objects.create(official_title=title, image=filename)
            AnimeAlias.objects.create(anime=anime_obj, title=title, do_initialize=True)
            logger.info ("Anime Added: " + title)