import type { Api } from './types'
import { createHttpApi } from './http'
import { createMockApi } from './mock/mockApi'

/** The mock build always uses fake data; the real build uses it only with ?mock in the URL. */
export const isMock = __BC_MOCK__ || new URLSearchParams(window.location.search).has('mock')

export const api: Api = isMock ? createMockApi() : createHttpApi()
