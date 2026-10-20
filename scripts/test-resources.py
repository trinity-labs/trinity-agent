"""Hardware policy and live resizing, using synthetic hosts and cgroups offline."""
from dataclasses import asdict, replace
import ast
import io
import json
import queue
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import ovh_resources as r
from ovh_cache import MemoryCache
from ovh_request import ChunkReaders, refresh_reader_limits

KEYS = ['RAM_CACHE_MB', 'RAM_CACHE_ENTRIES', 'PREFLIGHT_WORKERS', 'PREFLIGHT_MAX_FILES', 'PREFLIGHT_FILE_CHARS',
        'INDEX_CACHE_MB', 'ARCHIVE_CACHE_MB', 'MEMORY_DOCUMENT_CHARS']


class Resources(unittest.TestCase):
    def setUp(self):
        self.addCleanup(patch.stopall)
        patch.dict(os.environ, {'OVH_AGENT_' + key: 'auto' for key in KEYS}).start()
        patch.dict(os.environ, {'OVH_AGENT_PERFORMANCE_MODE': 'auto'}).start()

    def detect(self, files, cpus=32, affinity=16):
        with patch.object(r, '_read', side_effect=lambda path: files.get(str(path), '')), \
             patch.object(r.os, 'cpu_count', return_value=cpus), \
             patch.object(r.os, 'sched_getaffinity', return_value=set(range(affinity)), create=True):
            return r.detect_hardware()

    def memory(self, total=64, available=48):
        return {'/proc/meminfo': f'MemTotal: {total * 1024 * 1024} kB\nMemAvailable: {available * 1024 * 1024} kB'}

    def test_bare_host_and_affinity(self):
        hw = self.detect(self.memory(24, 20), cpus=8, affinity=6)
        profile = r.ResourcePolicy(lambda: hw).profile()
        self.assertEqual((profile.cpu_capacity, profile.workers), (6, 6))
        self.assertEqual(profile.cache_mb, 24 * 1024 // 5)
        self.assertEqual((profile.max_files, profile.file_chars), (720, 524288))

    def test_tiny_host_scales_down_every_resource(self):
        hw = r.Hardware(1, 512*r.MIB, 256*r.MIB)
        profile = r.ResourcePolicy(lambda: hw).profile()
        self.assertEqual(profile.workers, 1)
        self.assertLessEqual(profile.cache_mb, 64)
        self.assertLess(profile.max_files, 120)
        self.assertEqual(profile.file_chars, 65536)

    def test_large_server_respects_engine_bounds(self):
        profile = r.ResourcePolicy(lambda:r.Hardware(128,256*1024*r.MIB,240*1024*r.MIB)).profile()
        self.assertEqual(profile.workers,16)
        self.assertLessEqual(profile.max_files,2000)
        self.assertLessEqual(profile.cache_entries,65536)
        self.assertLessEqual(profile.file_chars,524288)

    def test_cgroup_v2_child_and_parent_limits_and_shared_usage(self):
        files = self.memory()
        files.update({
            '/proc/self/cgroup': '0::/tenant/agent',
            '/proc/self/mountinfo': '1 0 0:1 / /sys/fs/cgroup rw - cgroup2 cgroup rw',
            '/sys/fs/cgroup/tenant/agent/cpu.max': '400000 100000',
            '/sys/fs/cgroup/tenant/cpu.max': '150000 100000',
            '/sys/fs/cgroup/tenant/agent/cpuset.cpus.effective': '2-5,8',
            '/sys/fs/cgroup/tenant/agent/memory.max': str(1024*r.MIB),
            '/sys/fs/cgroup/tenant/agent/memory.current': str(100*r.MIB),
            '/sys/fs/cgroup/tenant/memory.max': str(2048*r.MIB),
            '/sys/fs/cgroup/tenant/memory.current': str(2000*r.MIB),
        })
        hw = self.detect(files)
        self.assertEqual(hw, r.Hardware(1.5, 1024*r.MIB, 48*r.MIB))
        profile = r.ResourcePolicy(lambda: hw).profile()
        self.assertEqual(profile.workers, 1)
        self.assertLessEqual(profile.cache_mb, 6)

    def test_cgroup_v2_namespace_root_and_fractional_cpu(self):
        files = self.memory()
        files.update({
            '/proc/self/cgroup': '0::/',
            '/proc/self/mountinfo': '1 0 0:1 / /sys/fs/cgroup ro - cgroup2 cgroup rw',
            '/sys/fs/cgroup/cpu.max': '50000 100000',
            '/sys/fs/cgroup/memory.max': str(512*r.MIB),
            '/sys/fs/cgroup/memory.current': str(400*r.MIB),
        })
        hw = self.detect(files)
        self.assertEqual(hw, r.Hardware(.5, 512*r.MIB, 112*r.MIB))
        self.assertEqual(r.ResourcePolicy(lambda: hw).profile().workers, 1)
        files['/proc/self/mountinfo']='1 0 0:1 /docker/container /sys/fs/cgroup ro - cgroup2 cgroup rw'
        self.assertEqual(self.detect(files), hw)

    def test_cgroup_v1_controller_mount_root_and_cpuset(self):
        files = self.memory()
        files.update({
            '/proc/self/cgroup': '2:cpu,cpuacct:/docker/app\n3:memory:/docker/app\n4:cpuset:/docker/app',
            '/proc/self/mountinfo': '\n'.join([
                '1 0 0:1 /docker /sys/fs/cgroup/cpu rw - cgroup cgroup rw,cpu,cpuacct',
                '2 0 0:2 /docker /sys/fs/cgroup/memory rw - cgroup cgroup rw,memory',
                '3 0 0:3 /docker /sys/fs/cgroup/cpuset rw - cgroup cgroup rw,cpuset']),
            '/sys/fs/cgroup/cpu/app/cpu.cfs_quota_us': '300000',
            '/sys/fs/cgroup/cpu/app/cpu.cfs_period_us': '100000',
            '/sys/fs/cgroup/cpuset/app/cpuset.cpus': '1,3',
            '/sys/fs/cgroup/memory/app/memory.limit_in_bytes': str(2048*r.MIB),
            '/sys/fs/cgroup/memory/app/memory.usage_in_bytes': str(1536*r.MIB),
        })
        self.assertEqual(self.detect(files), r.Hardware(2, 2048*r.MIB, 512*r.MIB))

    def test_memory_high_and_unlimited_constraints(self):
        files = self.memory()
        files.update({
            '/proc/self/cgroup': '0::/',
            '/proc/self/mountinfo': '1 0 0:1 / /sys/fs/cgroup rw - cgroup2 cgroup rw',
            '/sys/fs/cgroup/cpu.max': 'max 100000',
            '/sys/fs/cgroup/memory.max': 'max',
            '/sys/fs/cgroup/memory.high': str(2048*r.MIB),
            '/sys/fs/cgroup/memory.current': str(1800*r.MIB),
        })
        self.assertEqual(self.detect(files), r.Hardware(16, 2048*r.MIB, 248*r.MIB))
        files['/sys/fs/cgroup/memory.high'] = 'max'
        self.assertEqual(self.detect(files).memory_bytes, 64*1024*r.MIB)

    def test_missing_or_malformed_system_data_has_conservative_fallback(self):
        self.assertEqual(self.detect({}, cpus=None, affinity=1), r.Hardware(1,512*r.MIB,128*r.MIB))
        self.assertIsNone(r._cpu_set('2-z'))
        self.assertEqual(r._cpu_set('0-3,2-5,9'), 7)
        with patch.object(r.Path, 'read_text', side_effect=PermissionError):
            self.assertEqual(r._read('/proc/meminfo'), '')

    def test_manual_values_are_ceilings_and_zero_disables_cache_or_scan(self):
        policy = r.ResourcePolicy(lambda: r.Hardware(6,24*1024*r.MIB,20*1024*r.MIB))
        with patch.dict(os.environ, {'OVH_AGENT_RAM_CACHE_MB':'128','OVH_AGENT_RAM_CACHE_ENTRIES':'40',
                                    'OVH_AGENT_PREFLIGHT_WORKERS':'2','OVH_AGENT_PREFLIGHT_MAX_FILES':'50',
                                    'OVH_AGENT_PREFLIGHT_FILE_CHARS':'1024'}):
            profile = policy.profile()
            self.assertEqual((profile.cache_mb,profile.cache_entries,profile.workers,profile.max_files,profile.file_chars),
                             (128,40,2,50,1024))
        with patch.dict(os.environ, {'OVH_AGENT_RAM_CACHE_MB':'999999','OVH_AGENT_PREFLIGHT_WORKERS':'999999'}):
            self.assertLess(policy.profile().cache_mb,5000)
            self.assertEqual(policy.profile().workers,6)
        with patch.dict(os.environ, {'OVH_AGENT_RAM_CACHE_MB':'0','OVH_AGENT_PREFLIGHT_MAX_FILES':'0'}):
            self.assertEqual((policy.profile().cache_mb,policy.profile().cache_entries,policy.profile().max_files),(0,0,0))

    def test_invalid_settings_fall_back_to_auto(self):
        policy = r.ResourcePolicy(lambda: r.Hardware(2,2048*r.MIB,1024*r.MIB))
        expected = policy.profile()
        for value in ['', 'invalid', '-42']:
            with patch.dict(os.environ, {'OVH_AGENT_' + key:value for key in KEYS}):
                self.assertEqual(policy.profile(),expected)

    def test_index_archive_and_document_budgets_follow_memory_and_explicit_ceilings(self):
        large=r.ResourcePolicy(lambda:r.Hardware(6,24*1024*r.MIB,20*1024*r.MIB)).profile()
        small=r.ResourcePolicy(lambda:r.Hardware(1,512*r.MIB,256*r.MIB)).profile()
        for name in ('index_cache_mb','index_build_bytes','archive_cache_mb','memory_document_chars'):
            self.assertGreater(getattr(large,name),getattr(small,name))
        self.assertLessEqual(large.index_build_bytes*8,20*1024*r.MIB//16)
        policy=r.ResourcePolicy(lambda:r.Hardware(6,24*1024*r.MIB,20*1024*r.MIB))
        with patch.dict(os.environ,{'OVH_AGENT_INDEX_CACHE_MB':'2','OVH_AGENT_ARCHIVE_CACHE_MB':'1',
                                    'OVH_AGENT_MEMORY_DOCUMENT_CHARS':'12000'}):
            profile=policy.profile()
            self.assertEqual((profile.index_cache_mb,profile.archive_cache_mb,profile.memory_document_chars),(2,1,12000))
            self.assertLessEqual(profile.index_build_bytes,2*r.MIB)
        with patch.dict(os.environ,{'OVH_AGENT_INDEX_CACHE_MB':'0','OVH_AGENT_ARCHIVE_CACHE_MB':'0'}):
            profile=policy.profile()
            self.assertEqual((profile.index_cache_mb,profile.archive_cache_mb),(0,0))
            self.assertGreater(profile.index_build_bytes,0,'Disabled snapshots must not disable transient search')
        empty=r.ResourcePolicy(lambda:r.Hardware(1,512*r.MIB,0)).profile()
        self.assertEqual((empty.index_cache_mb,empty.archive_cache_mb,empty.index_build_bytes,empty.memory_document_chars),(0,0,0,0))

    def test_periodic_snapshot_reacts_to_pressure_and_recovers(self):
        clock = [0]
        hardware = [r.Hardware(8,16*1024*r.MIB,12*1024*r.MIB)]
        calls=[]
        def detect():calls.append(True);return hardware[0]
        policy = r.ResourcePolicy(detect)
        with patch.object(r.time,'monotonic',side_effect=lambda:clock[0]):
            large = policy.profile()
            hardware[0]=r.Hardware(1,512*r.MIB,32*r.MIB)
            clock[0]=10
            self.assertEqual(policy.profile(),large)
            clock[0]=31
            small = policy.profile(retained_bytes=1024*r.MIB)
            self.assertLessEqual(small.cache_mb,4)
            self.assertLessEqual(small.index_build_bytes,hardware[0].memory_bytes//128)
            self.assertEqual(small.workers,1)
            hardware[0]=r.Hardware(8,16*1024*r.MIB,12*1024*r.MIB)
            clock[0]=62
            self.assertEqual(policy.profile(),large)
        self.assertEqual(len(calls),3)

    def test_cache_releases_lru_entries_on_pressure_even_on_a_read(self):
        policy = r.ResourcePolicy(lambda:r.Hardware(4,4096*r.MIB,3072*r.MIB))
        large = replace(policy.profile(),cache_mb=3,cache_entries=10)
        small = replace(large,cache_mb=1,cache_entries=1)
        cache = MemoryCache(3*r.MIB,10,adaptive=True)
        with patch('ovh_cache.resource_profile',return_value=large):
            for key in ['a','b','c']: cache.put(key,1,key,r.MIB)
            self.assertEqual(cache.get('a',1),'a')
        cache.next_resize=0
        with patch('ovh_cache.resource_profile',return_value=small):
            self.assertEqual(cache.get('a',1),'a')
            self.assertEqual(cache.bytes,r.MIB)
            self.assertIsNone(cache.get('b',1))
        cache.next_resize=0
        with patch('ovh_cache.resource_profile',return_value=large):
            cache.put('d',1,'d',r.MIB)
            self.assertEqual(cache.bytes,2*r.MIB)

    def test_existing_reader_pool_reduces_concurrency_without_new_threads(self):
        pool = ChunkReaders(4)
        pool.resize(1)
        active = maximum = 0
        lock = threading.Lock()
        def read(path):
            nonlocal active, maximum
            with lock:active+=1;maximum=max(maximum,active)
            time.sleep(.005)
            with lock:active-=1
            return []
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);source=root/'source.py';source.write_text('test')
            with patch('ovh_request._file_chunks',side_effect=read):
                loaded,partial=pool.read([(source,0)]*8,root,root,time.monotonic()+2)
        self.assertFalse(partial)
        self.assertEqual(len(loaded),8)
        self.assertEqual(maximum,1)
        pool.resize(4)
        self.assertEqual(pool.created,4)

    def test_max_replaces_manual_and_fixed_caps_with_hardware_budgets(self):
        hw = r.Hardware(128,256*1024*r.MIB,240*1024*r.MIB)
        policy = r.ResourcePolicy(lambda:hw)
        auto = policy.profile()
        with patch.dict(os.environ, {'OVH_AGENT_' + key:'1' for key in KEYS}):
            policy.set_mode('max')
            maximum = policy.profile()
        self.assertEqual(maximum.mode, 'max')
        self.assertEqual(maximum.workers, 128)
        for name in ['cache_mb','cache_entries','max_files','file_chars']:
            self.assertGreater(getattr(maximum,name),getattr(auto,name))
        self.assertGreater(maximum.scan_files, 2000)
        self.assertEqual(maximum.refresh_seconds, 5)
        self.assertLessEqual((maximum.cache_mb+maximum.retrieval_mb)*r.MIB,hw.available_bytes-hw.memory_bytes//20)
        policy.set_mode('auto')
        self.assertEqual(policy.profile(),auto)
        with self.assertRaises(ValueError): policy.set_mode('unlimited')

    def test_max_honors_small_container_cpu_and_memory_pressure(self):
        for cpus,total,available in [(.5,512,256),(2,1024,48),(64,2048,128),(1,64,8)]:
            policy = r.ResourcePolicy(lambda:r.Hardware(cpus,total*r.MIB,available*r.MIB))
            policy.set_mode('max'); profile=policy.profile()
            self.assertLessEqual(profile.workers,max(1,int(cpus)))
            self.assertLessEqual(profile.cache_mb+profile.retrieval_mb,available)
            if available<=64:
                self.assertEqual((profile.cache_mb,profile.max_files,profile.file_chars),(0,0,0))
        files=self.memory()
        files.update({'/proc/self/cgroup':'0::/',
            '/proc/self/mountinfo':'1 0 0:1 / /sys/fs/cgroup ro - cgroup2 cgroup rw',
            '/sys/fs/cgroup/cpu.max':'150000 100000',
            '/sys/fs/cgroup/memory.max':str(1024*r.MIB),
            '/sys/fs/cgroup/memory.high':str(512*r.MIB),
            '/sys/fs/cgroup/memory.current':str(256*r.MIB)})
        policy=r.ResourcePolicy(lambda:self.detect(files));policy.set_mode('max')
        profile=policy.profile()
        self.assertEqual(profile.cpu_capacity,1.5)
        self.assertEqual(profile.memory_mb,512)
        self.assertEqual(profile.available_mb,256)
        self.assertLessEqual(profile.workers,2)

    def test_max_credits_only_cache_present_at_the_hardware_snapshot(self):
        hardware=[r.Hardware(8,16*1024*r.MIB,12*1024*r.MIB)]; clock=[0]
        policy=r.ResourcePolicy(lambda:hardware[0]);policy.set_mode('max')
        with patch.object(r.time,'monotonic',side_effect=lambda:clock[0]):
            initial=policy.profile()
            self.assertEqual(policy.profile(retained_bytes=6*1024*r.MIB),initial)
            clock[0]=6
            hardware[0]=r.Hardware(8,16*1024*r.MIB,6*1024*r.MIB)
            filled=policy.profile(retained_bytes=6*1024*r.MIB)
            self.assertEqual(filled.cache_mb,initial.cache_mb)
            self.assertEqual(policy.profile(retained_bytes=9*1024*r.MIB),filled)
            clock[0]=12
            hardware[0]=r.Hardware(1,512*r.MIB,16*r.MIB)
            small=policy.profile(retained_bytes=256*r.MIB)
            self.assertEqual(small.workers,1)
            self.assertLess(small.cache_mb,256)
            clock[0]=18
            hardware[0]=r.Hardware(8,16*1024*r.MIB,12*1024*r.MIB)
            self.assertEqual(policy.profile().cache_mb,initial.cache_mb)

    def test_max_reader_pool_can_use_more_than_sixteen_slots(self):
        pool=ChunkReaders(18); barrier=threading.Barrier(18)
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);source=root/'source.py';source.write_text('test')
            def read(path):
                barrier.wait(timeout=2)
                return [(1,1,'test',{'test':1})]
            with patch('ovh_request._file_chunks',side_effect=read):
                loaded,partial=pool.read([(source,0)]*18,root,root,time.monotonic()+3,max_bytes=r.MIB)
        self.assertFalse(partial)
        self.assertEqual(len(loaded),18)
        with patch('ovh_request._chunk_readers',pool), patch('ovh_request.resource_profile',return_value=SimpleNamespace(workers=1)):
            refresh_reader_limits()
        deadline=time.monotonic()+2
        while pool.created>1 and time.monotonic()<deadline:time.sleep(.02)
        self.assertEqual(pool.created,1)
        self.assertEqual(pool.jobs.maxsize,2)

    def test_reader_result_budget_and_thread_creation_failure_are_bounded(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);source=root/'source.py';source.write_text('test')
            pool=ChunkReaders(1)
            with patch('ovh_request._file_chunks',return_value=[(1,1,'x'*2000,{'x':1})]):
                loaded,partial=pool.read([(source,0)]*100,root,root,time.monotonic()+1,max_bytes=15000)
            self.assertTrue(partial)
            self.assertEqual(len(loaded),1)
        with patch('ovh_request.threading.Thread.start',side_effect=RuntimeError('no thread available')):
            unavailable=ChunkReaders(128)
        self.assertEqual(unavailable.created,0)
        self.assertEqual(unavailable.workers,1)

    def test_live_performance_control_refreshes_cache_without_starting_a_request(self):
        source=Path(__file__).resolve().parents[1]/'ovh-agent.py'
        nodes=[n for n in ast.parse(source.read_text()).body if isinstance(n,ast.FunctionDef)
               and n.name in {'handle_performance','stdin_reader'}]
        policy=r.ResourcePolicy(lambda:r.Hardware(8,8192*r.MIB,6144*r.MIB))
        cache=MemoryCache(0,adaptive=True);events=[];requests=queue.Queue()
        scope=dict(queue=queue,json=json,asdict=asdict,set_performance_mode=policy.set_mode,
                   resource_profile=policy.profile,CACHE=cache,emit=events.append,refresh_reader_limits=refresh_reader_limits,
                   sys=SimpleNamespace(stdin=io.StringIO('\n'.join(json.dumps({'type':'set_performance','mode':mode}) for mode in ['max','invalid','auto']))))
        exec(compile(ast.Module(body=nodes,type_ignores=[]),str(source),'exec'),scope)
        environment=dict(os.environ)
        with patch('ovh_cache.resource_profile',side_effect=policy.profile):scope['stdin_reader'](requests)
        self.assertEqual([event['type'] for event in events],['performance_status','performance_error','performance_status'])
        self.assertEqual([event['mode'] for event in events],['max','max','auto'])
        self.assertGreater(events[0]['cache_mb'],events[2]['cache_mb'])
        self.assertEqual(cache.max_bytes,events[2]['cache_mb']*r.MIB)
        self.assertEqual(requests.get_nowait(),{'type':'shutdown'})
        self.assertTrue(requests.empty())
        self.assertEqual(dict(os.environ),environment)


if __name__ == '__main__':
    unittest.main()
