import { flushPromises } from '@vue/test-utils';
import type { Ref } from 'vue';
import { ref } from 'vue';

import { useRequestFee } from '@/composables/useRequestFee';
import * as requestManagerService from '@/services/transactions/request-manager';
import { TokenAmount } from '@/types/token-amount';
import { UInt256 } from '@/types/uint-256';
import {
  generateTokenAmountData,
  generateUInt256Data,
  getRandomEthereumAddress,
} from '~/utils/data_generators';

vi.mock('@/services/transactions/request-manager');

const RPC_URL = ref('https://test.rpc');
const REQUEST_MANAGER_ADDRESS = ref(getRandomEthereumAddress());
const REQUEST_AMOUNT = ref(new TokenAmount(generateTokenAmountData())) as Ref<TokenAmount>;

describe('useRequestFee', () => {
  beforeEach(() => {
    Object.defineProperty(requestManagerService, 'getRequestFee', {
      value: vi.fn().mockResolvedValue(new UInt256(generateUInt256Data())),
    });

    global.console.error = vi.fn();
  });

  describe('amount', () => {
    it('should be undefined if the RPC URL is missing', () => {
      const { amount } = useRequestFee(ref(undefined), REQUEST_MANAGER_ADDRESS, REQUEST_AMOUNT);

      expect(amount.value).toBeUndefined();
    });

    it('should be undefined if the request manager address is missing', () => {
      const { amount } = useRequestFee(RPC_URL, ref(undefined), REQUEST_AMOUNT);

      expect(amount.value).toBeUndefined();
    });

    it('should be undefined if the request amount is missing', () => {
      const { amount } = useRequestFee(RPC_URL, REQUEST_MANAGER_ADDRESS, ref(undefined));

      expect(amount.value).toBeUndefined();
    });

    it('should be fetched from request manager', async () => {
      Object.defineProperty(requestManagerService, 'getRequestFee', {
        value: vi.fn().mockResolvedValue(new UInt256('99999')),
      });
      const requestAmount = ref(new TokenAmount(generateTokenAmountData())) as Ref<TokenAmount>;

      const { amount } = useRequestFee(RPC_URL, REQUEST_MANAGER_ADDRESS, requestAmount);
      await flushPromises();

      expect(amount.value).toEqual(
        new TokenAmount({ token: requestAmount.value.token, amount: '99999' }),
      );
    });

    it('should update itself when the request amount changes', async () => {
      const requestAmount = ref(new TokenAmount(generateTokenAmountData())) as Ref<TokenAmount>;

      const { amount } = useRequestFee(RPC_URL, REQUEST_MANAGER_ADDRESS, requestAmount);
      await flushPromises();

      Object.defineProperty(requestManagerService, 'getRequestFee', {
        value: vi.fn().mockResolvedValue(new UInt256('33')),
      });
      requestAmount.value = new TokenAmount(generateTokenAmountData());
      await flushPromises();

      expect(amount.value).toEqual(
        new TokenAmount({ token: requestAmount.value.token, amount: '33' }),
      );
    });
  });

  describe('error', () => {
    it('should include any fetching error', async () => {
      Object.defineProperty(requestManagerService, 'getRequestFee', {
        value: vi.fn().mockRejectedValue(new Error('error')),
      });

      const { error } = useRequestFee(RPC_URL, REQUEST_MANAGER_ADDRESS, REQUEST_AMOUNT);
      await flushPromises();

      expect(error.value).toBe('error');
    });

    it('should be empty if fetching succeeds', async () => {
      const { error } = useRequestFee(RPC_URL, REQUEST_MANAGER_ADDRESS, REQUEST_AMOUNT);
      await flushPromises();

      expect(error.value).toBe('');
    });
  });

  it('should delay execution of task when initialized as a debounced task', async () => {
    const delayInMillis = 500;
    useRequestFee(RPC_URL, REQUEST_MANAGER_ADDRESS, REQUEST_AMOUNT, true, delayInMillis);
    await flushPromises();
    expect(requestManagerService.getRequestFee).not.toHaveBeenCalled();
    await new Promise((r) => setTimeout(r, delayInMillis));
    expect(requestManagerService.getRequestFee).toHaveBeenCalledOnce();
  });
});
