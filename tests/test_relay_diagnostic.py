import unittest
from hashlib import sha1
from relay_diagnostic import transfer


class RelayTest(unittest.TestCase):
    def test_privacy_idempotence_and_readback(self):
        repo = 'test/private-relay'
        files = {'manifest.json':b'{"example":true}\n'}
        digest = 'a'*64
        writes = []
        state = {'private':False,'uploaded':False,'bad':False}
        def api(method,suffix='',body=None,missing=False):
            if method == 'GET' and not suffix:
                return {'full_name':repo,'private':state['private'],
                        'visibility':'private' if state['private'] else 'public','default_branch':'main'}
            if method == 'GET' and suffix == '/git/ref/heads/main':
                return {'object':{'sha':'new' if state['uploaded'] else 'old'}}
            if method == 'GET' and suffix.startswith('/contents/'):
                if not state['uploaded'] and missing:
                    return None
                return [{'name':name,'type':'file','sha':'wrong' if state['bad'] else sha1(b'blob '+str(len(data)).encode()+b'\0'+data).hexdigest()}
                        for name,data in files.items()]
            if method == 'GET' and suffix == '/git/commits/old':
                return {'tree':{'sha':'base'}}
            writes.append((method,suffix,body))
            if suffix == '/git/trees':
                return {'sha':'tree'}
            if suffix == '/git/commits':
                return {'sha':'new'}
            if suffix == '/git/refs/heads/main':
                state['uploaded'] = True
                return {}
            raise AssertionError((method,suffix))
        with self.assertRaisesRegex(ValueError,'relay_is_not_expected_private'):
            transfer(api,repo,digest,files)
        self.assertEqual(writes,[])
        state['private'] = True
        self.assertEqual(transfer(api,repo,digest,files)['status'],'uploaded_verified')
        self.assertEqual(len(writes),3)
        self.assertFalse(writes[-1][2]['force'])
        self.assertEqual(transfer(api,repo,digest,files)['status'],'already_present_verified')
        self.assertEqual(len(writes),3)
        state['bad'] = True
        with self.assertRaisesRegex(ValueError,'remote_diagnostic_hash_mismatch'):
            transfer(api,repo,digest,files)
        self.assertEqual(len(writes),3)


if __name__ == '__main__':
    unittest.main()
