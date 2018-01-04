###############################################################################
#
# Copyright (C) 2017 Andrew Muzikin
#
# This program is free software: you can redistribute it and/or modify
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
#
###############################################################################

import logging
#logging.basicConfig(format='%(name)s: %(message)s')

import datetime
import random
from numpy.random import beta as random_beta
import math
import os
import sys

import backtrader.feeds as btfeeds
import pandas as pd


class BTgymDataset:
    """
    Base Backtrader.feeds data class. Provides core data loading, sampling and converting functionality.
    Do not use directly.

    Enables Pipe::

        CSV[source data]-->pandas[for efficient sampling]-->bt.feeds

    """
    #  Parameters and their default values:
    params = dict(
        filename=None,  # Str or list of str, should be given either here  or when calling read_csv()

        # Default parameters for source-specific CSV datafeed class,
        # correctly parses 1 minute Forex generic ASCII
        # data files from www.HistData.com:

        # CSV to Pandas params.
        sep=';',
        header=0,
        index_col=0,
        parse_dates=True,
        names=['open', 'high', 'low', 'close', 'volume'],

        # Pandas to BT.feeds params:
        timeframe=1,  # 1 minute.
        datetime=0,
        open=1,
        high=2,
        low=3,
        close=4,
        volume=-1,
        openinterest=-1,

        # Sampling params:
        sample_class_ref=None,  # sample() method will return instance of this class.
        start_weekdays=[0, 1, 2, 3, ],  # Only weekdays from the list will be used for episode start.
        start_00=False,  # Sample start time will be set to first record of the day (usually 00:00).
        sample_duration=dict(  # Maximum sample time duration in days, hours, minutes:
            days=1,
            hours=23,
            minutes=55
        ),
        time_gap=dict(  # Maximum data time gap allowed within sample in days, hours. Thereby,
            days=0,     # if set to be < 1 day, samples containing weekends and holidays gaps will be rejected.
            hours=5,
        ),
        test_period=dict(  # Maximum sample time duration in days, hours, minutes:
            days=0,
            hours=0,
            minutes=0
        ),
        sample_expanding=None,
        sample_name='base_',
        metadata=dict(
            sample_num=0,
            type=0,
        ),
        log_level=None,
        task=0,
    )
    params_deprecated=dict(
        # Deprecated:
        episode_len_days=('episode_duration', 'days'),
        episode_len_hours=('episode_duration','hours'),
        episode_len_minutes=('episode_duration', 'minutes'),
        time_gap_days=('time_gap', 'days'),
        time_gap_hours=('time_gap', 'hours')
    )

    def __init__(self, **kwargs):
        """

        Args:

            filename:                       Str or list of str, should be given either here or when calling read_csv(),
                                            see `Notes`.

            specific_params CSV to Pandas parsing

            sep:                            ';'
            header:                         0
            index_col:                      0
            parse_dates:                    True
            names:                          ['open', 'high', 'low', 'close', 'volume']

            specific_params Pandas to BT.feeds conversion

            timeframe=1:                    1 minute.
            datetime:                       0
            open:                           1
            high:                           2
            low:                            3
            close:                          4
            volume:                         -1
            openinterest:                   -1

            specific_params Sampling

            sample_class_ref:               None - if not None, than sample() method will return instance of specified
                                            class, which itself must be subclass of BaseBTgymDataset,
                                            else returns instance of the generic BaseBTgymDataset.

            start_weekdays:                 [0, 1, 2, 3, ] - Only weekdays from the list will be used for episode start.
            start_00:                       True - Episode start time will be set to first record of the day
                                            (usually 00:00).
            sample_duration:                {'days': 1, 'hours': 23, 'minutes': 55} - Maximum sample time duration
                                            in days, hours, minutes
            episode_duration:               alias for sample_duration
            time_gap:                       {''days': 0, hours': 5, 'minutes': 0} - Data omittance threshold:
                                            maximum no-data time gap allowed within sample in days, hours.
                                            Thereby, if set to be < 1 day, samples containing weekends and holidays gaps
                                            will be rejected.
            test_period:                    {'days': 0, 'hours': 0, 'minutes': 0} - setting this param to non-zero
                                            duration forces instance.data split to train / test subsets with test
                                            subset duration equal to `test_period` with `time_gap` tolerance. Train data
                                            always precedes test one:
                                            [0_record<-train_data->split_point_record<-test_data->last_record].
            sample_expanding:               None, reserved for child classes.

        Note:
            - CSV file can contain duplicate records, checks will be performed and all duplicates will be removed;

            - CSV file should be properly sorted by date_time in ascending order, no sorting checks performed.

            - When supplying list of file_names, all files should be also listed ascending by their time period,
              no correct sampling will be possible otherwise.

            - Default parameters are source-specific and made to correctly parse 1 minute Forex generic ASCII
              data files from www.HistData.com. Tune according to your data source.
        """
        #self.log = None
        self.data = None  # Will hold actual data as pandas dataframe
        self.is_ready = False
        self.data_stat = None  # Dataset descriptive statistic as pandas dataframe
        self.data_range_delta = None  # Dataset total duration timedelta
        self.max_time_gap = None
        self.max_sample_len_delta = None
        self.sample_num_records = -1
        self.sample_class_ref = None
        self.sample_params={}
        self.metadata = {}
        self.test_range_delta = None
        self.train_range_delta = None
        self.test_num_records = 0
        self.train_num_records = 0
        self.train_interval = [0, 0]
        self.test_interval = [0, 0]
        self.sample_num = 0
        self.sample_name = 'should_not_see_this_'
        self.log_level =None
        self.task = 0

        # Logging:
        try:
            self.log_level = kwargs.pop('log_level')

        except KeyError:
            from logbook import WARNING
            self.log_level = WARNING

        from logbook import Logger, StreamHandler
        StreamHandler(sys.stdout).push_application()
        self.log = Logger('Dataset_{}'.format(self.task), level=self.log_level)

        self.params['log_level'] = self.log_level

        # Update parameters with relevant kwargs:
        self.update_params(**kwargs)
        self.sample_params.update(self.params)

    def reset(self, data_filename=None, **kwargs):
        """
        Gets instance ready.

        Args:
            data_filename:  [opt] string or list of strings.
            kwargs:         not used.

        Returns:

        """
        self.read_csv(data_filename)

        # Maximum data time gap allowed within sample as pydatetimedelta obj:
        self.max_time_gap = datetime.timedelta(**self.time_gap)

        # ... maximum episode time duration:
        self.max_sample_len_delta = datetime.timedelta(**self.sample_duration)

        # Maximum possible number of data records (rows) within episode:
        self.sample_num_records = int(self.max_sample_len_delta.total_seconds() / (60 * self.timeframe))

        # Train/test timedeltas:
        self.test_range_delta = datetime.timedelta(**self.test_period)
        #self.train_range_delta = datetime.timedelta(**self.sample_duration) - datetime.timedelta(**self.test_period)

        self.test_num_records = round(self.test_range_delta.total_seconds() / (60 * self.timeframe))
        self.train_num_records = self.data.shape[0] - self.test_num_records

        break_point = self.train_num_records

        assert self.train_num_records >= self.sample_num_records,\
            'Train subset should contain at least one sample, got: train_set size: {} rows, sample_size: {} rows'.\
            format(self.train_num_records, self.sample_num_records)
        if self.test_num_records > 0:
            assert self.test_num_records >= self.sample_num_records,\
                'Test subset should contain at least one sample, got: test_set size: {} rows, sample_size: {} rows'.\
            format(self.test_num_records, self.sample_num_records)

        self.train_interval = [0, break_point]
        self.test_interval = [break_point, self.data.shape[0]]

        self.sample_num = 0
        self.is_ready = True

    def update_params(self, **kwargs):
        """
        Updates instance parameters.

        Args:
            **kwargs:   any self.params entries
        """
        self.is_ready = False

        for key, value in kwargs.items():
            if key in self.params.keys():
                self.params[key] = value

            elif key in self.params_deprecated.keys():
                self.log.warning(
                    'Key: <{}> is deprecated, use: <{}> instead'.
                        format(key, self.params_deprecated[key])
                )
                key1, key2 = self.params_deprecated[key]
                self.params[key1][key2] = value

            elif key in ['episode_duration']:
                self.params[key]= value
                self.params['sample_duration'] = value

        # Unpack it as attributes:
        for key, value in self.params.items():
            setattr(self, key, value)

        # If no sampling class specified - make it base class:
        if self.params['sample_class_ref'] is None:
            self.params['sample_class_ref'] = self.sample_class_ref = BTgymDataset   # TODO: base class here

        else:
            self.sample_class_ref = self.params['sample_class_ref']

        # Update sample params:
        self.sample_params.update(self.params)

    def read_csv(self, data_filename=None, force_reload=False):
        """
        Populates instance by loading data: CSV file --> pandas dataframe.

        Args:
            data_filename: [opt] csv data filename as string or list of such strings.
            force_reload:  ignore loaded data.
        """
        if self.data is not None and not force_reload:
            self.log.debug('Dataset: data has been already loaded. Use `force_reload=True` to reload')
            return
        if data_filename:
            self.filename = data_filename  # override data source if one is given
        if type(self.filename) == str:
            self.filename = [self.filename]

        dataframes = []
        for filename in self.filename:
            try:
                assert filename and os.path.isfile(filename)
                current_dataframe = pd.read_csv(
                    filename,
                    sep=self.sep,
                    header=self.header,
                    index_col=self.index_col,
                    parse_dates=self.parse_dates,
                    names=self.names
                )

                # Check and remove duplicate datetime indexes:
                duplicates = current_dataframe.index.duplicated(keep='first')
                how_bad = duplicates.sum()
                if how_bad > 0:
                    current_dataframe = current_dataframe[~duplicates]
                    self.log.warning('Found {} duplicated date_time records in <{}>.\
                     Removed all but first occurrences.'.format(how_bad, filename))

                dataframes += [current_dataframe]
                self.log.info('Loaded {} records from <{}>.'.format(dataframes[-1].shape[0], filename))

            except:
                msg = 'Data file <{}> not specified / not found.'.format(str(filename))
                self.log.error(msg)
                raise FileNotFoundError(msg)

        self.data = pd.concat(dataframes)
        range = pd.to_datetime(self.data.index)
        self.data_range_delta = (range[-1] - range[0]).to_pytimedelta()

    def describe(self):
        """
        Returns summary dataset statistic as pandas dataframe:

            - records count,
            - data mean,
            - data std dev,
            - min value,
            - 25% percentile,
            - 50% percentile,
            - 75% percentile,
            - max value

        for every data column.
        """
        # Pretty straightforward, using standard pandas utility.
        # The only caveat here is that if actual data has not been loaded yet, need to load, describe and unload again,
        # thus avoiding passing big files to BT server:
        flush_data = False
        try:
            assert not self.data.empty
            pass

        except:
            self.read_csv()
            flush_data = True

        self.data_stat = self.data.describe()
        self.log.info('Data summary:\n{}'.format(self.data_stat.to_string()))

        if flush_data:
            self.data = None
            self.log.info('Flushed data.')

        return self.data_stat

    def to_btfeed(self):
        """
        Performs BTgymDataset-->bt.feed conversion.

        Returns:
             bt.datafeed instance.
        """
        try:
            assert not self.data.empty
            btfeed = btfeeds.PandasDirectData(
                dataname=self.data,
                timeframe=self.timeframe,
                datetime=self.datetime,
                open=self.open,
                high=self.high,
                low=self.low,
                close=self.close,
                volume=self.volume,
                openinterest=self.openinterest
            )
            btfeed.numrecords = self.data.shape[0]
            return btfeed

        except (AssertionError, AttributeError) as e:
            msg = 'Instance holds no data. Hint: forgot to call .read_csv()?'
            self.log.error(msg)
            raise AssertionError(msg)

    def sample(self, sample_type=0, b_alpha=1, b_beta=1, **kwargs):
        """
        Samples continuous subset of data.

        Args:
            sample_type:    int, def=0 (train) or 1 (test) - to sample from train or test data subsets respectively.
            b_alpha:        beta-distribution sampling alpha, valid for train episodes, def=1.
            b_beta:         beta-distribution sampling beta, valid for train episodes, def=1.

        Returns:
        if no sample_class_ref param been set:
            BTgymDataset instance with number of records ~ max_episode_len,
            where `~` tolerance is set by `time_gap` param;
        else:
            `sample_class_ref` instance with same as above number of records.

        """
        assert self.is_ready, 'Sampling attempt: data not ready. Hint: forgot to call data.reset()?'
        assert sample_type in [0, 1], 'Sampling attempt: expected sample type be in {}, got: {}'.\
            format([0, 1], sample_type)

        if sample_type == 0:
            # Get beta_distributed sample in train interval:
            sample = self._sample_interval(
                self.train_interval,
                b_alpha=b_alpha,
                b_beta=b_beta,
                name='train_' + self.sample_name
            )

        else:
            # Get uniform sample in test interval:
            sample = self._sample_interval(
                self.test_interval,
                b_alpha=1,
                b_beta=1,
                name='test_' + self.sample_name
            )

        sample.metadata['type'] = sample_type
        # TODO: renamed from 'episode_num', change in aac.py:
        sample.metadata['sample_num'] = self.sample_num
        self.sample_num += 1

        return sample

    def _sample_random(self, name='random_sample_'):
        """
        Randomly samples continuous subset of data.

        Args:
            name:        str, sample filename id

        Returns:
             BTgymDataset instance with number of records ~ max_episode_len,
             where `~` tolerance is set by `time_gap` param.
        """
        try:
            assert not self.data.empty

        except (AssertionError, AttributeError) as e:
            raise  AssertionError('Instance holds no data. Hint: forgot to call .read_csv()?')

        self.log.debug('Maximum sample time duration set to: {}.'.format(self.max_sample_len_delta))
        self.log.debug('Respective number of steps: {}.'.format(self.sample_num_records))
        self.log.debug('Maximum allowed data time gap set to: {}.\n'.format(self.max_time_gap))

        # Sanity check param:
        max_attempts = 100
        attempts = 0

        # # Keep sampling random enter points until all conditions are met:
        while attempts <= max_attempts:

            # Randomly sample record (row) from entire datafeed:
            first_row = int((self.data.shape[0] - self.sample_num_records - 1) * random.random())
            sample_first_day = self.data[first_row:first_row + 1].index[0]
            self.log.debug('Sample start: {}, weekday: {}.'.format(sample_first_day, sample_first_day.weekday()))

            # Keep sampling until good day:
            while not sample_first_day.weekday() in self.start_weekdays and attempts <= max_attempts:
                self.log.debug('Not a good day to start, resampling...')
                first_row = int((self.data.shape[0] - self.sample_num_records - 1) * random.random())
                sample_first_day = self.data[first_row:first_row + 1].index[0]
                self.log.debug('Sample start: {}, weekday: {}.'.format(sample_first_day, sample_first_day.weekday()))
                attempts +=1

            # Check if managed to get proper weekday:
            assert attempts <= max_attempts, \
                'Quitting after {} sampling attempts. Hint: check sampling params / dataset consistency.'. \
                format(attempts)

            # If 00 option set, get index of first record of that day:
            if self.start_00:
                adj_timedate = sample_first_day.date()
                self.log.debug('Start time adjusted to <00:00>')

            else:
                adj_timedate = sample_first_day

            first_row = self.data.index.get_loc(adj_timedate, method='nearest')

            # Easy part:
            last_row = first_row + self.sample_num_records  # + 1
            sampled_data = self.data[first_row: last_row]
            sample_len = (sampled_data.index[-1] - sampled_data.index[0]).to_pytimedelta()
            self.log.debug('Actual sample duration: {}.'.format(sample_len, ))
            self.log.debug('Total episode time gap: {}.'.format(sample_len - self.max_sample_len_delta))

            # Perform data gap check:
            if sample_len - self.max_sample_len_delta < self.max_time_gap:
                self.log.debug('Sample accepted.')
                # If sample OK - return sample:
                new_instance = self.sample_class_ref(**self.sample_params)
                new_instance.filename = name + str(adj_timedate)
                self.log.info('Sample id: <{}>.'.format(new_instance.filename))
                new_instance.data = sampled_data
                new_instance.metadata['type'] = 'random_sample'
                new_instance.metadata['first_row'] = first_row
                return new_instance

            else:
                self.log.debug('Duration too big, resampling...\n')
                attempts += 1

        # Got here -> sanity check failed:
        msg = ('Quitting after {} sampling attempts.' +
               'Hint: check sampling params / dataset consistency.').format(attempts)
        self.log.error(msg)
        raise RuntimeError(msg)

    def _sample_interval(self, interval, b_alpha=1, b_beta=1, name='interval_sample_'):
        """
        Samples continuous subset of data,
        such as entire episode records lie within positions specified by interval.
        Episode start position within interval is drawn from beta-distribution parametrised by `b_alpha, b_beta`.
        By default distribution is uniform one.

        Args:
            interval:       tuple, list or 1d-array of integers of length 2: [lower_row_number, upper_row_number];
            b_alpha:        float > 0, sampling B-distribution alpha param, def=1;
            b_beta:         float > 0, sampling B-distribution beta param, def=1;
            name:           str, sample filename id


        Returns:
             - BTgymDataset instance such as:
                1. number of records ~ max_episode_len, subj. to `time_gap` param;
                2. actual episode start position is sampled from `interval`;
             - `False` if it is not possible to sample instance with set args.
        """
        try:
            assert not self.data.empty

        except (AssertionError, AttributeError) as e:
            raise  AssertionError('Instance holds no data. Hint: forgot to call .read_csv()?')

        assert len(interval) == 2, 'Invalid interval arg: expected list or tuple of size 2, got: {}'.format(interval)

        assert b_alpha > 0 and b_beta > 0, 'Expected positive B-distribution [alpha, beta] params, got: {}'. \
            format([b_alpha, b_beta])

        sample_num_records = self.sample_num_records

        assert interval[0] < interval[-1] <= self.data.shape[0], \
            'Cannot sample with size {}, inside {} from dataset of {} records'.\
             format(sample_num_records, interval, self.data.shape[0])

        self.log.debug('Maximum sample time duration set to: {}.'.format(self.max_sample_len_delta))
        self.log.debug('Respective number of steps: {}.'.format(sample_num_records))
        self.log.debug('Maximum allowed data time gap set to: {}.\n'.format(self.max_time_gap))

        # Sanity check param:
        max_attempts = 100
        attempts = 0

        # # Keep sampling random enter points until all conditions are met:
        while attempts <= max_attempts:

            first_row = interval[0] + int(
                (interval[-1] - interval[0] - sample_num_records) * random_beta(a=b_alpha, b=b_beta)
            )

            #print('_sample_interval_sample_num_records: ', sample_num_records)
            #print('_sample_interval_first_row: ', first_row)

            sample_first_day = self.data[first_row:first_row + 1].index[0]
            self.log.debug('Sample start: {}, weekday: {}.'.format(sample_first_day, sample_first_day.weekday()))

            # Keep sampling until good day:
            while not sample_first_day.weekday() in self.start_weekdays and attempts <= max_attempts:
                self.log.debug('Not a good day to start, resampling...')
                first_row = interval[0] + round(
                    (interval[-1] - interval[0] - sample_num_records) * random_beta(a=b_alpha, b=b_beta)
                )
                #print('r_sample_interval_sample_num_records: ', sample_num_records)
                #print('r_sample_interval_first_row: ', first_row)
                sample_first_day = self.data[first_row:first_row + 1].index[0]
                self.log.debug('Sample start: {}, weekday: {}.'.format(sample_first_day, sample_first_day.weekday()))
                attempts += 1

            # Check if managed to get proper weekday:
            assert attempts <= max_attempts, \
                'Quitting after {} sampling attempts. Hint: check sampling params / dataset consistency.'.\
                format(attempts)

            # If 00 option set, get index of first record of that day:
            if self.start_00:
                adj_timedate = sample_first_day.date()
                self.log.debug('Start time adjusted to <00:00>')

            else:
                adj_timedate = sample_first_day

            first_row = self.data.index.get_loc(adj_timedate, method='nearest')

            # Easy part:
            last_row = first_row + sample_num_records  # + 1
            sampled_data = self.data[first_row: last_row]
            sample_len = (sampled_data.index[-1] - sampled_data.index[0]).to_pytimedelta()
            self.log.debug('Actual sample duration: {}.'.format(sample_len))
            self.log.debug('Total sample time gap: {}.'.format(sample_len - self.max_sample_len_delta))

            # Perform data gap check:
            if sample_len - self.max_sample_len_delta < self.max_time_gap:
                self.log.debug('Sample accepted.')
                # If sample OK - return episodic-dataset:
                new_instance = self.sample_class_ref(**self.sample_params)
                new_instance.filename = name + str(adj_timedate)
                self.log.info('Sample id: <{}>.'.format(new_instance.filename))
                new_instance.data = sampled_data
                new_instance.metadata['type'] = 'interval_sample'
                new_instance.metadata['first_row'] = first_row
                return new_instance

            else:
                self.log.debug('Attempt {}: duration too big, resampling, ...\n'.format(attempts))
                attempts += 1

        # Got here -> sanity check failed:
        msg = ('Quitting after {} sampling attempts.' +
               'Hint: check sampling params / dataset consistency.').format(attempts)
        self.log.warning(msg)
        raise AssertionError(msg)


class BTgymBaseDataTrial(BTgymDataset):
    """
    Base Data trial class. Do not use directly. Appears as result of sampling from one of DataDomain classes.
    Holds train and test saubsets.
    """
    episode_params=dict(
        # Episode sampling params:
        sample_duration=dict(  # Maximum episode time duration in days, hours, minutes:
            days=1,
            hours=23,
            minutes=55
        ),
        time_gap=dict(  # Maximum data time gap allowed within sample in days, hours. Thereby,
            days=0,  # if set to be < 1 day, samples containing weekends and holidays gaps will be rejected.
            hours=5,
        ),
        start_00=False,
        start_weekdays=[0, 1, 2, 3, 4],
        sample_class_ref=BTgymDataset
    )

    def __init__(self, episode_params=None, **kwargs):
        if episode_params is not None:
            self.episode_params.update(episode_params)

        super(BTgymBaseDataTrial, self).__init__(**kwargs)

        self.update_params(self.episode_params)
        self.sample_params = self.params

        # Timedeltas:
        self.train_range_delta = datetime.timedelta(**self.train_period)
        self.test_range_delta = datetime.timedelta(**self.test_period)
        self.train_num_records = round(self.train_range_delta.total_seconds() / (60 * self.timeframe))
        self.test_num_records = round(self.test_range_delta.total_seconds() / (60 * self.timeframe))

        self.train_interval = -1
        self.test_interval = -1
        self.sample_num = -1
        self.metadata = {}

    def reset(self, data_filename=None, **kwargs):
        """
        Gets data ready.

        Args:
            data_filename:  [opt] string or list of strings.
            kwargs:         not used.

        Returns:

        """
        self.read_csv(data_filename)

        # Get train/test bound scaled to actual data size:
        break_point = round(
            self.train_num_records * self.data.shape[0] /(self.train_num_records + self.test_num_records)
        )
        self.train_interval = [0, break_point]
        self.test_interval = [break_point + 1, self.data.shape[0]]
        self.sample_num = 0
        self.is_ready = True

    def sample(self, sample_type='train', b_alpha=1, b_beta=1):
        """
        Samples episode from Trial.

        Args:
            sample_type:    str, either def='train' or 'test'
            b_alpha:        beta-distribution sampling alpha, valid for train episodes, def=1
            b_beta:         beta-distribution sampling beta, valid for train episodes, def=1

        Returns:
            episode as BTgymDataSet instance

        """
        assert self.is_ready, 'Sampling attempt: Trial data not ready. Hint: forgot to call reset()?'
        assert sample_type in ['train', 'test'], 'Sampling attempt: expected episode type be in {}, got: {}'.\
            format(['train', 'test'], sample_type)

        if sample_type in 'train':
            # Get beta_distributed sample in train interval:
            episode = self._sample_interval(self.train_interval, b_alpha=b_alpha, b_beta=b_beta, name='train_episode_')

        else:
            # Get uniform sample in test interval:
            episode = self._sample_interval(self.test_interval, b_alpha=1, b_beta=1, name='test_episode_')

        episode.metadata['type'] = sample_type
        # TODO: rename to 'episode_num', change in aac.py:
        episode.metadata['sample_num'] = self.sample_num
        self.sample_num +=1

        return episode


class BTgymBaseDataDomain(BTgymDataset):
    """
    Base Data domain class. Do not use directly.
    """
    trial_params = dict(
        # Trial sampling params:
        train_period=dict(  # Trial time range in days, hours, minutes:
            days=30,
            hours=0,
        ),
        test_period=dict(  # Test time period in days, hours, minutes:
            days=2,
            hours=0,
        ),
        time_gap=dict(  # Maximum data gap in days, hours, minutes:
            days=15,
            hours=0,
        ),
        start_00=True,
        start_weekdays=[0, 1, 2, 3, 4, 5, 6],
        sample_expanding=False,
        sample_class_ref=BTgymBaseDataTrial,
    )
    episode_params = {}

    def __init__(self, trial_params=None, **kwargs):
        if trial_params is not None:
            self.trial_params.update(trial_params)

        super(BTgymBaseDataDomain, self).__init__(**kwargs)

        self.update_params(self.trial_params)

        self.sample_params = {}
        self.sample_params.update(trial_params)
        self.sample_params.update(kwargs)

        self.metadata = {}

        # Sample of this class is Trial, so:
        #self.start_weekdays = self.params['trial_start_weekdays']
        #self.start_00=self.params['trial_start_00']
        #self.time_gap = self.params['trial_time_gap']

    def update_params(self, kwargs):
        """
        Updates instance parameters.

        Args:
            **kwargs:   any self.params entries
        """
        self.is_ready = False

        for key, value in kwargs.items():
            if key in self.params.keys():
                print('key_{} was_{} became_{}'.format(key, self.params[key], value))
                self.params[key] = value

            elif key in self.params_deprecated.keys():
                self.log.warning(
                    'Key: <{}> is deprecated, use: <{}> instead'.
                        format(key, self.params_deprecated[key])
                )
                key1, key2 = self.params_deprecated[key]
                self.params[key1][key2] = value

        # Unpack it as attributes:
        for key, value in self.params.items():
            setattr(self, key, value)

        # If no sampling class specified - make it own class:
        if self.params['sample_class_ref'] is None:
            self.params['sample_class_ref'] = self.sample_class_ref = BTgymBaseDataTrial

        else:
            self.sample_class_ref = self.trial_params['sample_class_ref']

        # Timedeltas:
        self.train_range_delta = datetime.timedelta(**self.train_period)
        self.test_range_delta = datetime.timedelta(**self.test_period)

        # ...maximum Trial time duration:
        self.max_sample_len_delta = self.train_range_delta + self.test_range_delta

        # Maximum data time gap allowed within Trial sample as pydatetimedelta obj:
        self.max_time_gap = datetime.timedelta(**self.time_gap)

        # Maximum possible number of data records (rows) within trial:
        self.sample_num_records = int(self.max_sample_len_delta.total_seconds() / (60 * self.timeframe))


BTgymBaseData = BTgymDataset

