
# Copyright (c) 2013 Calin Crisan
# This file is part of motionEye.
#
# motionEye is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
# 
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
# 
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>. 

import errno
import logging
import os.path
import re

from collections import OrderedDict

import diskctl
import motionctl
import settings
import smbctl
import tzctl
import update
import utils
import v4l2ctl
import wifictl


_CAMERA_CONFIG_FILE_NAME = 'thread-%(id)s.conf'
_MAIN_CONFIG_FILE_NAME = 'motion.conf'

_main_config_cache = None
_camera_config_cache = {}
_camera_ids_cache = None

# starting with r490 motion config directives have changed a bit 
_LAST_OLD_CONFIG_VERSIONS = (490, '3.2.12')


def get_main(as_lines=False):
    global _main_config_cache
    
    if not as_lines and _main_config_cache is not None:
        return _main_config_cache
    
    config_file_path = os.path.join(settings.CONF_PATH, _MAIN_CONFIG_FILE_NAME)
    
    logging.debug('reading main config from file %(path)s...' % {'path': config_file_path})
    
    lines = None
    try:
        file = open(config_file_path, 'r')
    
    except IOError as e:
        if e.errno == errno.ENOENT:  # file does not exist
            logging.info('main config file %(path)s does not exist, using default values' % {'path': config_file_path})
            
            lines = []
        
        else:
            logging.error('could not open main config file %(path)s: %(msg)s' % {
                    'path': config_file_path, 'msg': unicode(e)})
            
            raise
    
    if lines is None:
        try:
            lines = [l[:-1] for l in file.readlines()]
        
        except Exception as e:
            logging.error('could not read main config file %(path)s: %(msg)s' % {
                    'path': config_file_path, 'msg': unicode(e)})
            
            raise
        
        finally:
            file.close()
    
    if as_lines:
        return lines
    
    main_config = _conf_to_dict(lines, list_names=['thread'])
    
    if settings.WPA_SUPPLICANT_CONF:
        _get_wifi_settings(main_config)
        
    if settings.LOCAL_TIME_FILE:
        _get_localtime_settings(main_config)
        
    _set_default_motion(main_config)
    
    _main_config_cache = main_config
    
    return main_config


def set_main(main_config):
    global _main_config_cache
    
    _set_default_motion(main_config)
    _main_config_cache = dict(main_config)

    _set_wifi_settings(main_config)
    _set_localtime_settings(main_config)
        
    config_file_path = os.path.join(settings.CONF_PATH, _MAIN_CONFIG_FILE_NAME)
    
    # read the actual configuration from file
    lines = get_main(as_lines=True)
    
    # write the configuration to file
    logging.debug('writing main config to %(path)s...' % {'path': config_file_path})
    
    try:
        file = open(config_file_path, 'w')
    
    except Exception as e:
        logging.error('could not open main config file %(path)s for writing: %(msg)s' % {
                'path': config_file_path, 'msg': unicode(e)})
        
        raise
    
    lines = _dict_to_conf(lines, main_config, list_names=['thread'])
    
    try:
        file.writelines([l + '\n' for l in lines])
    
    except Exception as e:
        logging.error('could not write main config file %(path)s: %(msg)s' % {
                'path': config_file_path, 'msg': unicode(e)})
        
        raise
    
    finally:
        file.close()


def get_camera_ids():
    global _camera_ids_cache
    
    if _camera_ids_cache is not None:
        return _camera_ids_cache

    config_path = settings.CONF_PATH
    
    logging.debug('listing config dir %(path)s...' % {'path': config_path})
    
    try:
        ls = os.listdir(config_path)
    
    except Exception as e:
        logging.error('failed to list config dir %(path)s: %(msg)s', {
                'path': config_path, 'msg': unicode(e)})
        
        raise
    
    camera_ids = []
    
    pattern = '^' + _CAMERA_CONFIG_FILE_NAME.replace('%(id)s', '(\d+)') + '$'
    for name in ls:
        match = re.match(pattern, name)
        if match:
            camera_id = int(match.groups()[0])
            logging.debug('found camera with id %(id)s' % {
                    'id': camera_id})
            
            camera_ids.append(camera_id)
        
    camera_ids.sort()
    
    filtered_camera_ids = []
    for camera_id in camera_ids:
        if get_camera(camera_id):
            filtered_camera_ids.append(camera_id)
    
    _camera_ids_cache = filtered_camera_ids
    
    return filtered_camera_ids


def has_enabled_cameras():
    if not get_main().get('@enabled'):
        return False
    
    camera_ids = get_camera_ids()
    cameras = [get_camera(camera_id) for camera_id in camera_ids]
    return bool([c for c in cameras if c.get('@enabled') and utils.local_camera(c)])


def get_network_shares():
    if not get_main().get('@enabled'):
        return []

    camera_ids = get_camera_ids()
    cameras = [get_camera(camera_id) for camera_id in camera_ids]
    
    mounts = []
    for camera in cameras:
        if camera.get('@storage_device') != 'network-share':
            continue
        
        mounts.append({
            'server': camera['@network_server'],
            'share': camera['@network_share_name'],
            'username': camera['@network_username'],
            'password': camera['@network_password'],
        })
        
    return mounts


def get_camera(camera_id, as_lines=False):
    global _camera_config_cache
    
    if not as_lines and camera_id in _camera_config_cache:
        return _camera_config_cache[camera_id]
    
    camera_config_path = os.path.join(settings.CONF_PATH, _CAMERA_CONFIG_FILE_NAME) % {'id': camera_id}
    
    logging.debug('reading camera config from %(path)s...' % {'path': camera_config_path})
    
    try:
        file = open(camera_config_path, 'r')
    
    except Exception as e:
        logging.error('could not open camera config file: %(msg)s' % {'msg': unicode(e)})
        
        raise
    
    try:
        lines = [l.strip() for l in file.readlines()]
    
    except Exception as e:
        logging.error('could not read camera config file %(path)s: %(msg)s' % {
                'path': camera_config_path, 'msg': unicode(e)})
        
        raise
    
    finally:
        file.close()
    
    if as_lines:
        return lines
        
    camera_config = _conf_to_dict(lines)
    
    if utils.local_camera(camera_config):
        # determine the enabled status
        main_config = get_main()
        threads = main_config.get('thread', [])
        camera_config['@enabled'] = _CAMERA_CONFIG_FILE_NAME % {'id': camera_id} in threads
        camera_config['@id'] = camera_id
        
        old_motion = _is_old_motion()
        
        # adapt directives from old configuration, if needed
        if old_motion:
            logging.debug('using old motion config directives')
            
            if 'output_normal' in camera_config:
                camera_config['output_pictures'] = camera_config.pop('output_normal')
            if 'output_all' in camera_config:
                camera_config['emulate_motion'] = camera_config.pop('output_all')
            if 'ffmpeg_cap_new' in camera_config:
                camera_config['ffmpeg_output_movies'] = camera_config.pop('ffmpeg_cap_new')
            if 'locate' in camera_config:
                camera_config['locate_motion_mode'] = camera_config.pop('locate')
            if 'jpeg_filename' in camera_config:
                camera_config['picture_filename'] = camera_config.pop('jpeg_filename')
            if 'webcam_port' in camera_config:
                camera_config['stream_port'] = camera_config.pop('webcam_port')
            if 'webcam_quality' in camera_config:
                camera_config['stream_quality'] = camera_config.pop('webcam_quality')
            if 'webcam_motion' in camera_config:
                camera_config['stream_motion'] = camera_config.pop('webcam_motion')
            if 'webcam_maxrate' in camera_config:
                camera_config['stream_maxrate'] = camera_config.pop('webcam_maxrate')
            if 'webcam_localhost' in camera_config:
                camera_config['stream_localhost'] = camera_config.pop('webcam_localhost')
            if 'gap' in camera_config:
                camera_config['event_gap'] = camera_config.pop('gap')
                
        _set_default_motion_camera(camera_id, camera_config)
    
    elif utils.remote_camera(camera_config):
        pass
    
    else: # incomplete configuration
        logging.warn('camera config file at %s is incomplete, ignoring' % camera_config_path)
        
        return None
    
    _camera_config_cache[camera_id] = dict(camera_config)
    
    return camera_config


def set_camera(camera_id, camera_config):
    global _camera_config_cache

    camera_config = dict(camera_config)
    camera_config['@id'] = camera_id
    _camera_config_cache[camera_id] = camera_config
    
    if utils.local_camera(camera_config):
        old_motion = _is_old_motion()
        
        # adapt directives to old configuration, if needed
        if old_motion:
            logging.debug('using old motion config directives')
            
            if 'output_pictures' in camera_config:
                camera_config['output_normal'] = camera_config.pop('output_pictures')
            if 'emulate_motion' in camera_config:
                camera_config['output_all'] = camera_config.pop('emulate_motion')
            if 'ffmpeg_output_movies' in camera_config:
                camera_config['ffmpeg_cap_new'] = camera_config.pop('ffmpeg_output_movies')
            if 'locate_motion_mode' in camera_config:
                camera_config['locate'] = camera_config.pop('locate_motion_mode')
            if 'picture_filename' in camera_config:
                camera_config['jpeg_filename'] = camera_config.pop('picture_filename')
            if 'stream_port' in camera_config:
                camera_config['webcam_port'] = camera_config.pop('stream_port')
            if 'stream_quality' in camera_config:
                camera_config['webcam_quality'] = camera_config.pop('stream_quality')
            if 'stream_motion' in camera_config:
                camera_config['webcam_motion'] = camera_config.pop('stream_motion')
            if 'stream_maxrate' in camera_config:
                camera_config['webcam_maxrate'] = camera_config.pop('stream_maxrate')
            if 'stream_localhost' in camera_config:
                camera_config['webcam_localhost'] = camera_config.pop('stream_localhost')
            if 'event_gap' in camera_config:
                camera_config['gap'] = camera_config.pop('event_gap')
            
            camera_config['netcam_tolerant_check'] = True
        
        _set_default_motion_camera(camera_id, camera_config, old_motion)
        
        # set the enabled status in main config
        main_config = get_main()
        threads = main_config.setdefault('thread', [])
        config_file_name = _CAMERA_CONFIG_FILE_NAME % {'id': camera_id}
        if camera_config['@enabled'] and config_file_name not in threads:
            threads.append(config_file_name)
                
        elif not camera_config['@enabled']:
            threads = [t for t in threads if t != config_file_name]

        main_config['thread'] = threads
        
        set_main(main_config)
        
    # read the actual configuration from file
    config_file_path = os.path.join(settings.CONF_PATH, _CAMERA_CONFIG_FILE_NAME) % {'id': camera_id}
    if os.path.isfile(config_file_path):
        lines = get_camera(camera_id, as_lines=True)
    
    else:
        lines = []
    
    # write the configuration to file
    camera_config_path = os.path.join(settings.CONF_PATH, _CAMERA_CONFIG_FILE_NAME) % {'id': camera_id}
    logging.debug('writing camera config to %(path)s...' % {'path': camera_config_path})
    
    try:
        file = open(camera_config_path, 'w')
    
    except Exception as e:
        logging.error('could not open camera config file %(path)s for writing: %(msg)s' % {
                'path': camera_config_path, 'msg': unicode(e)})
        
        raise
    
    lines = _dict_to_conf(lines, camera_config)
    
    try:
        file.writelines([l + '\n' for l in lines])
    
    except Exception as e:
        logging.error('could not write camera config file %(path)s: %(msg)s' % {
                'path': camera_config_path, 'msg': unicode(e)})
        
        raise
    
    finally:
        file.close()
        

def add_camera(device_details):
    global _camera_ids_cache
    global _camera_config_cache
    
    # determine the last camera id
    camera_ids = get_camera_ids()

    camera_id = 1
    while camera_id in camera_ids:
        camera_id += 1
    
    logging.info('adding new camera with id %(id)s...' % {'id': camera_id})
    
    # prepare a default camera config
    camera_config = {'@enabled': True}
    if device_details['proto'] == 'v4l2':
        _set_default_motion_camera(camera_id, camera_config)
        camera_config_ui = camera_dict_to_ui(camera_config)
        camera_config_ui.update(device_details)
        camera_config = camera_ui_to_dict(camera_config_ui)
    
    elif device_details['proto'] == 'motioneye':
        camera_config['@proto'] = 'motioneye'
        camera_config['@host'] = device_details['host']
        camera_config['@port'] = device_details['port']
        camera_config['@uri'] = device_details['uri']
        camera_config['@username'] = device_details['username']
        camera_config['@password'] = device_details['password']
        camera_config['@remote_camera_id'] = device_details['remote_camera_id']

    else: # assuming netcam
        camera_config['netcam_url'] = 'http://dummy' # used only to identify it as a netcam
        _set_default_motion_camera(camera_id, camera_config)
        camera_config_ui = camera_dict_to_ui(camera_config)
        camera_config_ui.update(device_details)
        camera_config = camera_ui_to_dict(camera_config_ui)

    # write the configuration to file
    set_camera(camera_id, camera_config)
    
    _camera_ids_cache = None
    _camera_config_cache = {}
    
    camera_config = get_camera(camera_id)
    
    return camera_id, camera_config


def rem_camera(camera_id):
    global _camera_ids_cache
    global _camera_config_cache
    
    camera_config_name = _CAMERA_CONFIG_FILE_NAME % {'id': camera_id}
    camera_config_path = os.path.join(settings.CONF_PATH, _CAMERA_CONFIG_FILE_NAME) % {'id': camera_id}
    
    # remove the camera from the main config
    main_config = get_main()
    threads = main_config.setdefault('thread', [])
    threads = [t for t in threads if t != camera_config_name]
    
    main_config['thread'] = threads

    set_main(main_config)
    
    logging.info('removing camera config file %(path)s...' % {'path': camera_config_path})
    
    _camera_ids_cache = None
    _camera_config_cache = {}
    
    try:
        os.remove(camera_config_path)
    
    except Exception as e:
        logging.error('could not remove camera config file %(path)s: %(msg)s' % {
                'path': camera_config_path, 'msg': unicode(e)})
        
        raise


def main_ui_to_dict(ui):
    return {
        '@enabled': ui['enabled'],
        
        '@show_advanced': ui['show_advanced'],
        '@admin_username': ui['admin_username'],
        '@admin_password': ui['admin_password'],
        '@normal_username': ui['normal_username'],
        '@normal_password': ui['normal_password'],
        '@time_zone': ui['time_zone'],
        
        '@wifi_enabled': ui['wifi_enabled'],
        '@wifi_name': ui['wifi_name'],
        '@wifi_key': ui['wifi_key'],
    }


def main_dict_to_ui(data):
    return {
        'enabled': data['@enabled'],
        
        'show_advanced': data['@show_advanced'],
        'admin_username': data['@admin_username'],
        'admin_password': data['@admin_password'],
        'normal_username': data['@normal_username'],
        'normal_password': data['@normal_password'],
        'time_zone': data['@time_zone'],
    
        'wifi_enabled': data['@wifi_enabled'],
        'wifi_name': data['@wifi_name'],
        'wifi_key': data['@wifi_key'],
    }


def camera_ui_to_dict(ui):
    data = {
        # device
        '@name': ui['name'],
        '@enabled': ui['enabled'],
        'lightswitch': int(ui['light_switch_detect']) * 50,
        'framerate': int(ui['framerate']),
        'rotate': int(ui['rotation']),
        
        # file storage
        '@storage_device': ui['storage_device'],
        '@network_server': ui['network_server'],
        '@network_share_name': ui['network_share_name'],
        '@network_username': ui['network_username'],
        '@network_password': ui['network_password'],
        
        # text overlay
        'text_left': '',
        'text_right': '',
        'text_double': False,
        
        # streaming
        'stream_localhost': not ui['video_streaming'],
        'stream_port': int(ui['streaming_port']),
        'stream_maxrate': int(ui['streaming_framerate']),
        'stream_quality': max(1, int(ui['streaming_quality'])),
        '@webcam_resolution': max(1, int(ui['streaming_resolution'])),
        '@webcam_server_resize': ui['streaming_server_resize'],
        'stream_motion': ui['streaming_motion'],
        
        # still images
        'output_pictures': False,
        'emulate_motion': False,
        'snapshot_interval': 0,
        'picture_filename': '',
        'snapshot_filename': '',
        '@preserve_pictures': int(ui['preserve_pictures']),
        
        # movies
        'ffmpeg_output_movies': ui['motion_movies'],
        'movie_filename': ui['movie_file_name'],
        'ffmpeg_bps': 400000,
        '@preserve_movies': int(ui['preserve_movies']),
    
        # motion detection
        'text_changes': ui['show_frame_changes'],
        'locate_motion_mode': ui['show_frame_changes'],
        'noise_tune': ui['auto_noise_detect'],
        'noise_level': max(1, int(round(int(ui['noise_level']) * 2.55))),
        'event_gap': int(ui['event_gap']),
        'pre_capture': int(ui['pre_capture']),
        'post_capture': int(ui['post_capture']),
        
        # working schedule
        '@working_schedule': '',
    
        # events
        'on_event_start': ''
    }
    
    if ui['proto'] == 'v4l2':
        # device
        data['videodevice'] = ui['uri']
        
        # resolution
        if not ui['resolution']:
            ui['resolution'] = '320x240'

        width = int(ui['resolution'].split('x')[0])
        height = int(ui['resolution'].split('x')[1])
        data['width'] = width
        data['height'] = height
        
        threshold = int(float(ui['frame_change_threshold']) * width * height / 100)

        if 'brightness' in ui:
            if int(ui['brightness']) == 50:
                data['brightness'] = 0
                
            else:
                data['brightness'] = max(1, int(round(int(ui['brightness']) * 2.55)))
        
        if 'contrast' in ui:
            if int(ui['contrast']) == 50:
                data['contrast'] = 0
                
            else:
                data['contrast'] = max(1, int(round(int(ui['contrast']) * 2.55)))
        
        if 'saturation' in ui:
            if int(ui['saturation']) == 50:
                data['saturation'] = 0
                
            else:
                data['saturation'] = max(1, int(round(int(ui['saturation']) * 2.55)))
            
        if 'hue' in ui:
            if int(ui['hue']) == 50:
                data['hue'] = 0
                
            else:
                data['hue'] = max(1, int(round(int(ui['hue']) * 2.55)))
    
    else: # assuming http/netcam
        # device
        data['netcam_url'] = ui['proto'] + '://' + ui['host']
        if ui['port']:
            data['netcam_url'] += ':' + ui['port']
        
        data['netcam_url'] += ui['uri']
        
        if ui['username'] or ui['password']:
            data['netcam_userpass'] = (ui['username'] or '') + ':' + (ui['password'] or '')

        data['netcam_http'] = '1.1'
        
        threshold = int(float(ui['frame_change_threshold']) * 640 * 480 / 100)

    data['threshold'] = threshold

    if (ui['storage_device'] == 'network-share') and settings.SMB_SHARES:
        mount_point = smbctl.make_mount_point(ui['network_server'], ui['network_share_name'], ui['network_username'])
        if ui['root_directory'].startswith('/'):
            ui['root_directory'] = ui['root_directory'][1:]
        data['target_dir'] = os.path.normpath(os.path.join(mount_point, ui['root_directory']))
    
    elif ui['storage_device'].startswith('local-disk'):
        target_dev = ui['storage_device'][10:].replace('-', '/')
        mounted_partitions = diskctl.list_mounted_partitions()
        partition = mounted_partitions[target_dev]
        mount_point = partition['mount_point']
        
        if ui['root_directory'].startswith('/'):
            ui['root_directory'] = ui['root_directory'][1:]
        data['target_dir'] = os.path.normpath(os.path.join(mount_point, ui['root_directory']))

    else:
        data['target_dir'] = ui['root_directory']

    if ui['text_overlay']:
        left_text = ui['left_text']
        if left_text == 'camera-name':
            data['text_left'] = ui['name']
            
        elif left_text == 'timestamp':
            data['text_left'] = '%Y-%m-%d\\n%T'
            
        else:
            data['text_left'] = ui['custom_left_text']
        
        right_text = ui['right_text']
        if right_text == 'camera-name':
            data['text_right'] = ui['name']
            
        elif right_text == 'timestamp':
            data['text_right'] = '%Y-%m-%d\\n%T'
            
        else:
            data['text_right'] = ui['custom_right_text']
        
        if ui['proto'] != 'v4l2' or data['width'] > 320:
            data['text_double'] = True
    
    if ui['still_images']:
        capture_mode = ui['capture_mode']
        if capture_mode == 'motion-triggered':
            data['output_pictures'] = True
            data['picture_filename'] = ui['image_file_name']  
            
        elif capture_mode == 'interval-snapshots':
            data['snapshot_interval'] = int(ui['snapshot_interval'])
            data['snapshot_filename'] = ui['image_file_name']
            
        elif capture_mode == 'all-frames':
            data['emulate_motion'] = True
            data['picture_filename'] = ui['image_file_name']
            
        data['quality'] = max(1, int(ui['image_quality']))
    
    if ui['motion_movies']:
        if ui['proto'] == 'v4l2':
            max_val = data['width'] * data['height'] * data['framerate'] / 3
        
        else:
            max_val = 640 * 480 * data['framerate'] / 3
            
        max_val = min(max_val, 9999999)
        
        data['ffmpeg_bps'] = int(ui['movie_quality']) * max_val / 100
    
    # working schedule
    if ui['working_schedule']:
        data['@working_schedule'] = (
                ui['monday_from'] + '-' + ui['monday_to'] + '|' + 
                ui['tuesday_from'] + '-' + ui['tuesday_to'] + '|' + 
                ui['wednesday_from'] + '-' + ui['wednesday_to'] + '|' + 
                ui['thursday_from'] + '-' + ui['thursday_to'] + '|' + 
                ui['friday_from'] + '-' + ui['friday_to'] + '|' + 
                ui['saturday_from'] + '-' + ui['saturday_to'] + '|' + 
                ui['sunday_from'] + '-' + ui['sunday_to'])
    
    # event start notifications
    on_event_start = []
    if ui['email_notifications_enabled']:
        send_mail_path = os.path.join(settings.PROJECT_PATH, 'sendmail.py')
        send_mail_path = os.path.abspath(send_mail_path)
        emails = re.sub('\\s', '', ui['email_notifications_addresses'])
        
        on_event_start.append('%(script)s "%(server)s" "%(port)s" "%(account)s" "%(password)s" "%(tls)s" "%(to)s" "motion_start" "%%t" "%%Y-%%m-%%dT%%H:%%M:%%S"' % {
                'script': send_mail_path,
                'server': ui['email_notifications_smtp_server'],
                'port': ui['email_notifications_smtp_port'],
                'account': ui['email_notifications_smtp_account'],
                'password': ui['email_notifications_smtp_password'],
                'tls': ui['email_notifications_smtp_tls'],
                'to': emails})

    if ui['web_hook_notifications_enabled']:
        web_hook_path = os.path.join(settings.PROJECT_PATH, 'webhook.py')
        web_hook_path = os.path.abspath(web_hook_path)
        url = re.sub('\\s', '+', ui['web_hook_notifications_url'])

        on_event_start.append('%(script)s "%(method)s" "%(url)s"' % {
                'script': web_hook_path,
                'method': ui['web_hook_notifications_http_method'],
                'url': url})

    if ui['command_notifications_enabled']:
        commands = ui['command_notifications_exec'].split(';')
        on_event_start += [c.strip() for c in commands]

    if on_event_start:
        data['on_event_start'] = '; '.join(on_event_start)

    return data


def camera_dict_to_ui(data):
    ui = {
        # device
        'name': data['@name'],
        'enabled': data['@enabled'],
        'id': data['@id'],
        'light_switch_detect': data['lightswitch'] > 0,
        'framerate': int(data['framerate']),
        'rotation': int(data['rotate']),
        
        # file storage
        'smb_shares': settings.SMB_SHARES,
        'storage_device': data['@storage_device'],
        'network_server': data['@network_server'],
        'network_share_name': data['@network_share_name'],
        'network_username': data['@network_username'],
        'network_password': data['@network_password'],
        'disk_used': 0,
        'disk_total': 0,
        'available_disks': diskctl.list_mounted_disks(),

        # text overlay
        'text_overlay': False,
        'left_text': 'camera-name',
        'right_text': 'timestamp',
        'custom_left_text': '',
        'custom_right_text': '',
        
        # streaming
        'video_streaming': not data['stream_localhost'],
        'streaming_framerate': int(data['stream_maxrate']),
        'streaming_quality': int(data['stream_quality']),
        'streaming_resolution': int(data['@webcam_resolution']),
        'streaming_server_resize': int(data['@webcam_server_resize']),
        'streaming_port': int(data['stream_port']),
        'streaming_motion': int(data['stream_motion']),
        
        # still images
        'still_images': False,
        'capture_mode': 'motion-triggered',
        'image_file_name': '%Y-%m-%d/%H-%M-%S',
        'image_quality': 85,
        'snapshot_interval': 0,
        'preserve_pictures': data['@preserve_pictures'],
        
        # motion movies
        'motion_movies': data['ffmpeg_output_movies'],
        'movie_file_name': data['movie_filename'],
        'preserve_movies': data['@preserve_movies'],

        # motion detection
        'show_frame_changes': data['text_changes'] or data['locate_motion_mode'],
        'auto_noise_detect': data['noise_tune'],
        'noise_level': int(int(data['noise_level']) / 2.55),
        'event_gap': int(data['event_gap']),
        'pre_capture': int(data['pre_capture']),
        'post_capture': int(data['post_capture']),
        
        # motion notifications
        'email_notifications_enabled': False,
        'web_hook_notifications_enabled': False,
        'command_notifications_enabled': False,
        
        # working schedule
        'working_schedule': False,
        'monday_from': '09:00', 'monday_to': '17:00',
        'tuesday_from': '09:00', 'tuesday_to': '17:00',
        'wednesday_from': '09:00', 'wednesday_to': '17:00',
        'thursday_from': '09:00', 'thursday_to': '17:00',
        'friday_from': '09:00', 'friday_to': '17:00',
        'saturday_from': '09:00', 'saturday_to': '17:00',
        'sunday_from': '09:00', 'sunday_to': '17:00'
    }
    
    if utils.net_camera(data):
        netcam_url = data.get('netcam_url')
        proto, rest = netcam_url.split('://')
        parts = rest.split('/', 1)
        if len(parts) > 1:
            host_port, uri = parts[:2]
            uri = '/' + uri
        
        else:
            host_port, uri = rest, ''
        
        parts = host_port.split(':')
        if len(parts) > 1:
            host, port = parts[:2]
        
        else:
            host, port = host_port, ''

        ui['proto'] = proto
        ui['host'] = host
        ui['port'] = port
        ui['uri'] = uri
        
        userpass = data.get('netcam_userpass')
        if userpass:
            ui['username'], ui['password'] = userpass.split(':', 1)
        
        else:
            ui['username'], ui['password'] = '', ''
        
        # width & height are not available for netcams,
        # we have no other choice but use something like 640x480 as reference
        threshold = data['threshold'] * 100.0 / (640 * 480)
    
    else: # assuming v4l2
        ui['proto'] = 'v4l2'
        ui['host'], ui['port'] = None, None
        ui['uri'] = data['videodevice']
        ui['username'], ui['password'] = None, None

        # resolutions
        resolutions = v4l2ctl.list_resolutions(data['videodevice'])
        ui['available_resolutions'] = [(str(w) + 'x' + str(h)) for (w, h) in resolutions]
        ui['resolution'] = str(data['width']) + 'x' + str(data['height'])
    
        # the brightness & co. keys in the ui dictionary
        # indicate the presence of these controls
        # we must call v4l2ctl functions to determine the available controls    
        brightness = v4l2ctl.get_brightness(ui['uri'])
        if brightness is not None: # has brightness control
            if data.get('brightness', 0) != 0:
                ui['brightness'] = brightness
                    
            else:
                ui['brightness'] = 50

        contrast = v4l2ctl.get_contrast(ui['uri'])
        if contrast is not None: # has contrast control
            if data.get('contrast', 0) != 0:
                ui['contrast'] = contrast
            
            else:
                ui['contrast'] = 50
            
        saturation = v4l2ctl.get_saturation(ui['uri'])
        if saturation is not None: # has saturation control
            if data.get('saturation', 0) != 0:
                ui['saturation'] = saturation
            
            else:
                ui['saturation'] = 50
            
        hue = v4l2ctl.get_hue(ui['uri'])
        if hue is not None: # has hue control
            if data.get('hue', 0) != 0:
                ui['hue'] = hue
            
            else:
                ui['hue'] = 50
        
        threshold = data['threshold'] * 100.0 / (data['width'] * data['height'])
        
    ui['frame_change_threshold'] = threshold
    
    if (data['@storage_device'] == 'network-share') and settings.SMB_SHARES:
        mount_point = smbctl.make_mount_point(data['@network_server'], data['@network_share_name'], data['@network_username'])
        ui['root_directory'] = data['target_dir'][len(mount_point):] or '/'
    
    elif data['@storage_device'].startswith('local-disk'):
        target_dev = data['@storage_device'][10:].replace('-', '/')
        mounted_partitions = diskctl.list_mounted_partitions()
        for partition in mounted_partitions.values():
            if partition['target'] == target_dev and data['target_dir'].startswith(partition['mount_point']):
                ui['root_directory'] = data['target_dir'][len(partition['mount_point']):] or '/'
                break

        else: # not found for some reason
            logging.error('could not find mounted partition for device "%s" and target dir "%s"' % (target_dev, data['target_dir']))
            ui['root_directory'] = data['target_dir']

    else:
        ui['root_directory'] = data['target_dir']

    # disk usage
    usage = utils.get_disk_usage(data['target_dir'])
    if usage:
        ui['disk_used'], ui['disk_total'] = usage
    
    text_left = data['text_left']
    text_right = data['text_right'] 
    if text_left or text_right:
        ui['text_overlay'] = True
        
        if text_left == data['@name']:
            ui['left_text'] = 'camera-name'
            
        elif text_left == '%Y-%m-%d\\n%T':
            ui['left_text'] = 'timestamp'
            
        else:
            ui['left_text'] = 'custom-text'
            ui['custom_left_text'] = text_left

        if text_right == data['@name']:
            ui['right_text'] = 'camera-name'
            
        elif text_right == '%Y-%m-%d\\n%T':
            ui['right_text'] = 'timestamp'
            
        else:
            ui['right_text'] = 'custom-text'
            ui['custom_right_text'] = text_right

    emulate_motion = data['emulate_motion']
    output_pictures = data['output_pictures']
    picture_filename = data['picture_filename']
    snapshot_interval = data['snapshot_interval']
    snapshot_filename = data['snapshot_filename']
    
    if (((emulate_motion or output_pictures) and picture_filename) or
        (snapshot_interval and snapshot_filename)):
        
        ui['still_images'] = True
        
        if emulate_motion:
            ui['capture_mode'] = 'all-frames'
            ui['image_file_name'] = picture_filename
            
        elif snapshot_interval:
            ui['capture_mode'] = 'interval-snapshots'
            ui['image_file_name'] = snapshot_filename
            ui['snapshot_interval'] = snapshot_interval
            
        elif output_pictures:
            ui['capture_mode'] = 'motion-triggered'
            ui['image_file_name'] = picture_filename  
            
        ui['image_quality'] = data['quality']

    ffmpeg_bps = data['ffmpeg_bps']
    if ffmpeg_bps is not None:
        if utils.v4l2_camera(data):
            max_val = data['width'] * data['height'] * data['framerate'] / 3
        
        else:
            max_val = 640 * 480 * data['framerate'] / 3
            
        max_val = min(max_val, 9999999)
        
        ui['movie_quality'] = min(100, int(round(ffmpeg_bps * 100.0 / max_val)))

    # working schedule
    working_schedule = data['@working_schedule']
    if working_schedule:
        days = working_schedule.split('|')
        ui['monday_from'], ui['monday_to'] = days[0].split('-')
        ui['tuesday_from'], ui['tuesday_to'] = days[1].split('-')
        ui['wednesday_from'], ui['wednesday_to'] = days[2].split('-')
        ui['thursday_from'], ui['thursday_to'] = days[3].split('-')
        ui['friday_from'], ui['friday_to'] = days[4].split('-')
        ui['saturday_from'], ui['saturday_to'] = days[5].split('-')
        ui['sunday_from'], ui['sunday_to'] = days[6].split('-')
        ui['working_schedule'] = True
    
    # event start notifications    
    on_event_start = data.get('on_event_start') or []
    if on_event_start:
        on_event_start = [e.strip() for e in on_event_start.split(';')]

    command_notifications = []
    for e in on_event_start:
        if e.count('sendmail.py') and e.count('motion_start'):
            e = e.replace('"', '').split(' ')
            if len(e) != 10:
                continue

            ui['email_notifications_enabled'] = True 
            ui['email_notifications_smtp_server'] = e[1]
            ui['email_notifications_smtp_port'] = e[2]
            ui['email_notifications_smtp_account'] = e[3]
            ui['email_notifications_smtp_password'] = e[4]
            ui['email_notifications_smtp_tls'] = e[5].lower() == 'true'
            ui['email_notifications_addresses'] = e[6]

        elif e.count('webhook.py'):
            e = e.replace('"', '').split(' ')
            if len(e) != 3:
                continue

            ui['web_hook_notifications_enabled'] = True 
            ui['web_hook_notifications_http_method'] = e[1]
            ui['web_hook_notifications_url'] = e[2]

        else: # custom command
            command_notifications.append(e)
    
    if command_notifications: 
        ui['command_notifications_enabled'] = True
        ui['command_notifications_exec'] = '; '.join(command_notifications)

    return ui


def _value_to_python(value):
    value_lower = value.lower()
    if value_lower == 'off':
        return False
    
    elif value_lower == 'on':
        return True
    
    try:
        return int(value)
    
    except ValueError:
        try:
            return float(value)
        
        except ValueError:
            return value


def _python_to_value(value):
    if value is True:
        return 'on'
    
    elif value is False:
        return 'off'
    
    elif isinstance(value, (int, float)):
        return str(value)
    
    else:
        return value


def _conf_to_dict(lines, list_names=[]):
    data = OrderedDict()
    
    for line in lines:
        line = line.strip()
        if len(line) == 0:  # empty line
            continue
        
        if line.startswith(';'):  # comment line
            continue
        
        match = re.match('^\#\s*(\@\w+)\s*([^\#]*)', line)
        if match:
            name, value = match.groups()[:2]
        
        elif line.startswith('#'): # comment line
            continue

        else:
            line = line.split('#')[0] # everything up to the first #
            
            parts = line.split(None, 1)
            if len(parts) != 2:  # invalid line format
                continue

            (name, value) = parts
            value = value.strip()
        
        value = _value_to_python(value)
        
        if name in list_names:
            data.setdefault(name, []).append(value)
        
        else:
            data[name] = value

    return data


def _dict_to_conf(lines, data, list_names=[]):
    conf_lines = []
    remaining = OrderedDict(data)
    processed = set()
    
    # parse existing lines and replace the values
    
    for line in lines:
        line = line.strip()
        if len(line) == 0:  # empty line
            conf_lines.append(line)
            continue

        if line.startswith(';'):  # simple comment line
            conf_lines.append(line)
            continue
        
        match = re.match('^\#\s*(\@\w+)\s*([^\#]*)', line)
        if match: # @line
            (name, value) = match.groups()[:2]
        
        elif line.startswith('#'):  # simple comment line
            conf_lines.append(line)
            continue
        
        else:
            parts = line.split(None, 1)
            if len(parts) == 2:
                (name, value) = parts
            
            else:
                (name, value) = parts[0], ''
        
        if name in processed:
            continue # name already processed
        
        processed.add(name)
        
        if name in list_names:
            new_value = data.get(name)
            if new_value is not None:
                for v in new_value:
                    line = name + ' ' + _python_to_value(v)
                    conf_lines.append(line)
            
            else:
                line = name + ' ' + value
                conf_lines.append(line)

        else:
            new_value = data.get(name)
            if new_value is not None:
                value = _python_to_value(new_value)
            
            line = name + ' ' + value
            conf_lines.append(line)
        
        remaining.pop(name, None)
    
    # add the remaining config values not covered by existing lines
    
    if len(remaining) and len(lines):
        conf_lines.append('') # add a blank line
    
    for (name, value) in remaining.iteritems():
        if name in list_names:
            for v in value:
                line = name + ' ' + _python_to_value(v)
                conf_lines.append(line)

        else:
            line = name + ' ' + _python_to_value(value)
            conf_lines.append(line)
            
    # build the final config lines
    conf_lines.sort(key=lambda l: not l.startswith('@'))
    
    lines = []
    for i, line in enumerate(conf_lines):
        # squeeze successive blank lines
        if i > 0 and len(line.strip()) == 0 and len(conf_lines[i - 1].strip()) == 0:
            continue
        
        if line.startswith('@'):
            line = '# ' + line
        
        elif i > 0 and conf_lines[i - 1].startswith('@'):
            lines.append('') # add a blank line between @lines and the rest
        
        lines.append(line)
        
    return lines


def _is_old_motion():
    try:
        binary, version = motionctl.find_motion()  # @UnusedVariable
        
        if version.startswith('trunkREV'): # e.g. trunkREV599
            version = int(version[8:])
            return version < _LAST_OLD_CONFIG_VERSIONS[0]
        
        else: # stable release, should be in the format x.y.z
            return update.compare_versions(version, _LAST_OLD_CONFIG_VERSIONS[1]) <= 0

    except:
        return False


def _set_default_motion(data):
    data.setdefault('@enabled', True)
    
    data.setdefault('@show_advanced', False)
    data.setdefault('@admin_username', 'admin')
    data.setdefault('@admin_password', '')
    data.setdefault('@normal_username', 'user')
    data.setdefault('@normal_password', '')
    data.setdefault('@time_zone', 'UTC')

    data.setdefault('@wifi_enabled', False)
    data.setdefault('@wifi_name', '')
    data.setdefault('@wifi_key', '')


def _set_default_motion_camera(camera_id, data, old_motion=False):
    data.setdefault('@name', 'Camera' + str(camera_id))
    data.setdefault('@enabled', False)
    data.setdefault('@id', camera_id)
    
    if not utils.net_camera(data):
        data.setdefault('videodevice', '/dev/video0')
        data.setdefault('brightness', 0)
        data.setdefault('contrast', 0)
        data.setdefault('saturation', 0)
        data.setdefault('hue', 0)
        data.setdefault('width', 352)
        data.setdefault('height', 288)

    data.setdefault('lightswitch', 50)
    data.setdefault('framerate', 2)
    data.setdefault('rotate', 0)
    
    data.setdefault('@storage_device', 'custom-path')
    data.setdefault('@network_server', '')
    data.setdefault('@network_share_name', '')
    data.setdefault('@network_username', '')
    data.setdefault('@network_password', '')
    data.setdefault('target_dir', settings.RUN_PATH)
    
    if old_motion:
        data.setdefault('webcam_localhost', False)
        data.setdefault('webcam_port', int('808' + str(camera_id)))
        data.setdefault('webcam_maxrate', 5)
        data.setdefault('webcam_quality', 85)
        data.setdefault('webcam_motion', False)
    
    else:
        data.setdefault('stream_localhost', False)
        data.setdefault('stream_port', int('808' + str(camera_id)))
        data.setdefault('stream_maxrate', 5)
        data.setdefault('stream_quality', 85)
        data.setdefault('stream_motion', False)

    data.setdefault('@webcam_resolution', 100)
    data.setdefault('@webcam_server_resize', False)
    
    data.setdefault('text_left', data['@name'])
    data.setdefault('text_right', '%Y-%m-%d\\n%T')
    data.setdefault('text_double', False)

    data.setdefault('text_changes', False)
    if old_motion:
        data.setdefault('locate', False)
    
    else:
        data.setdefault('locate_motion_mode', False)
        data.setdefault('locate_motion_style', 'redbox')
    
    data.setdefault('threshold', 2000)
    data.setdefault('noise_tune', True)
    data.setdefault('noise_level', 32)
    data.setdefault('minimum_motion_frames', 1)
    
    data.setdefault('pre_capture', 2)
    data.setdefault('post_capture', 4)
    
    if old_motion:
        data.setdefault('output_normal', False)
        data.setdefault('jpeg_filename', '')
        data.setdefault('output_all', False)
        data.setdefault('gap', 30)

    else:
        data.setdefault('output_pictures', False)
        data.setdefault('picture_filename', '')
        data.setdefault('emulate_motion', False)
        data.setdefault('event_gap', 30)
    
    data.setdefault('snapshot_interval', 0)
    data.setdefault('snapshot_filename', '')
    data.setdefault('quality', 85)
    data.setdefault('@preserve_pictures', 0)
    
    data.setdefault('ffmpeg_variable_bitrate', 0)
    data.setdefault('ffmpeg_bps', 400000)
    data.setdefault('movie_filename', '%Y-%m-%d/%H-%M-%S')
    if old_motion:
        data.setdefault('ffmpeg_cap_new', False)
    
    else:
        data.setdefault('ffmpeg_output_movies', False)
    data.setdefault('ffmpeg_video_codec', 'msmpeg4')
    data.setdefault('@preserve_movies', 0)
    
    data.setdefault('@working_schedule', '')

    data.setdefault('on_event_start', '')


def _get_wifi_settings(data):
    wifi_settings = wifictl.get_wifi_settings()

    data['@wifi_enabled'] = bool(wifi_settings['ssid'])
    data['@wifi_name'] = wifi_settings['ssid']
    data['@wifi_key'] = wifi_settings['psk']
    

def _set_wifi_settings(data):
    wifi_enabled = data.pop('@wifi_enabled', False)
    wifi_name = data.pop('@wifi_name', '')
    wifi_key = data.pop('@wifi_key', '')
    
    if settings.WPA_SUPPLICANT_CONF:
        s = {
            'ssid': wifi_enabled and wifi_name,
            'psk': wifi_key
        }
        
        wifictl.set_wifi_settings(s)


def _get_localtime_settings(data):
    time_zone = tzctl.get_time_zone()
    data['@time_zone'] = time_zone


def _set_localtime_settings(data):
    time_zone = data.pop('@time_zone')
    if time_zone and settings.LOCAL_TIME_FILE:
        tzctl.set_time_zone(time_zone)
